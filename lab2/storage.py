"""Единое SQLite-хранилище объектной модели и журнал действий платформы."""
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from uuid import UUID

from model import (AnalyticData, AnalyticsService, AuthService, Category, Comment,
                   CommentService, CommentValidator, Event, EventParticipant, EventService,
                   Notification, NotificationService, ParticipatnService, User, minsk_now)


@dataclass
class Services:
    auth: AuthService
    events: EventService
    participants: ParticipatnService
    analytics: AnalyticsService
    notifications: NotificationService
    comments: CommentService

    def base(self):
        """Возвращает основные сервисы в порядке конструкторов интерфейса."""
        return self.auth, self.events, self.participants, self.analytics


class StateStore:
    def __init__(self, path: str | Path):
        """Открывает базу и применяет первую версию схемы, не удаляя существующие данные."""
        self.path = str(path)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 1:
                raise RuntimeError("Версия базы новее версии приложения.")
            if version == 0:
                db.executescript('''
                BEGIN;
                CREATE TABLE users(id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    role TEXT NOT NULL, password TEXT NOT NULL, full_name TEXT NOT NULL, active INTEGER NOT NULL);
                CREATE TABLE categories(id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE);
                CREATE TABLE events(id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                    date TEXT NOT NULL, location TEXT NOT NULL, organizer TEXT REFERENCES users(id),
                    category TEXT NOT NULL REFERENCES categories(id), analytics_id TEXT NOT NULL, views INTEGER NOT NULL);
                CREATE TABLE registrations(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE, status TEXT NOT NULL,
                    UNIQUE(user_id,event_id));
                CREATE TABLE sessions(token TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    expires REAL NOT NULL, csrf TEXT NOT NULL);
                CREATE TABLE notifications(id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    description TEXT NOT NULL, kind TEXT NOT NULL, read INTEGER NOT NULL);
                CREATE TABLE subscriptions(user_id TEXT REFERENCES users(id), event_id TEXT REFERENCES events(id) ON DELETE CASCADE,
                    PRIMARY KEY(user_id,event_id));
                CREATE TABLE delivered(user_id TEXT REFERENCES users(id), event_id TEXT REFERENCES events(id) ON DELETE CASCADE,
                    date TEXT, PRIMARY KEY(user_id,event_id,date));
                CREATE TABLE comments(id TEXT PRIMARY KEY, event_id TEXT NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                    author TEXT NOT NULL REFERENCES users(id), text TEXT NOT NULL);
                CREATE TABLE audit(id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL,
                    actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL);
                PRAGMA user_version=1;
                COMMIT;
                ''')

    @contextmanager
    def connect(self):
        """Открывает соединение с проверкой внешних ключей и именованными колонками."""
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def load(self) -> Services:
        """Восстанавливает связанные объекты и отдельный контекст авторизации для одного запроса."""
        a = AuthService(); e = EventService(a); p = ParticipatnService(a, e)
        n = NotificationService(a, e); c = CommentService(CommentValidator([]))
        with self.connect() as db:
            for r in db.execute("SELECT * FROM users"):
                u = User(UUID(r['id']), r['username'], r['role'], r['password'], r['full_name'], bool(r['active']))
                a.users[u.username] = u
            users = {str(u.id): u for u in a.users.values()}
            rows = list(db.execute("SELECT * FROM categories"))
            if rows:
                e.categories = {r['name']: Category(UUID(r['id']), r['name']) for r in rows}
            categories = {str(g.id): g for g in e.categories.values()}
            for r in db.execute("SELECT * FROM events"):
                event = Event(UUID(r['id']), r['title'], r['description'], datetime.fromisoformat(r['date']),
                              r['location'], AnalyticData(UUID(r['analytics_id']), r['views']), users.get(r['organizer']))
                e.events[event.id] = event
                categories[r['category']].events.append(event)
            for r in db.execute("SELECT * FROM registrations"):
                item = EventParticipant(UUID(r['id']), r['status'], users[r['user_id']], e.events[UUID(r['event_id'])])
                p.registrations[(item.user.id, item.event.id)] = item
            for r in db.execute("SELECT * FROM sessions"):
                a.sessions[r['token']] = {'user': r['user_id'], 'expires': r['expires'], 'csrf': r['csrf']}
            for r in db.execute("SELECT * FROM notifications ORDER BY rowid"):
                n.notifications.append(Notification(UUID(r['id']), r['description'], e.events[UUID(r['event_id'])],
                                                    users[r['user_id']], r['kind'], bool(r['read'])))
            n.subscriptions = {(UUID(r['user_id']), UUID(r['event_id'])) for r in db.execute("SELECT * FROM subscriptions")}
            n.delivered = {(UUID(r['user_id']), UUID(r['event_id']), r['date']) for r in db.execute("SELECT * FROM delivered")}
            for r in db.execute("SELECT * FROM comments ORDER BY rowid"):
                c.comments.setdefault(UUID(r['event_id']), []).append(Comment(UUID(r['id']), r['text'], users[r['author']]))
        return Services(a, e, p, AnalyticsService(e), n, c)

    def save(self, s: Services, audit: tuple[str, str, str] | None = None) -> None:
        """Атомарно сохраняет граф объектов и запись журнала; зависимые записи удалённых событий исключаются."""
        with self.connect() as db:
            for table in ('comments', 'delivered', 'subscriptions', 'notifications', 'sessions', 'registrations', 'events', 'categories', 'users'):
                db.execute(f"DELETE FROM {table}")
            db.executemany("INSERT INTO users VALUES (?,?,?,?,?,?)", [(str(u.id), u.username, u.role, u.password, u.fullName, int(u.active)) for u in s.auth.users.values()])
            db.executemany("INSERT INTO categories VALUES (?,?)", [(str(g.id), g.name) for g in s.events.categories.values()])
            for event in s.events.events.values():
                category = s.events.categories[s.events.categoryFor(event)]
                db.execute("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?)", (str(event.id), event.title, event.description,
                           event.eventDateTime.isoformat(), event.location, str(event.organizer.id) if event.organizer else None,
                           str(category.id), str(event.analytics.id), event.analytics.viewsCount))
            valid = s.events.events
            db.executemany("INSERT INTO registrations VALUES (?,?,?,?)", [(str(r.id), str(r.user.id), str(r.event.id), r.status) for r in s.participants.registrations.values() if r.event.id in valid])
            now = datetime.now().timestamp()
            db.executemany("INSERT INTO sessions VALUES (?,?,?,?)", [(token, v['user'], v['expires'], v['csrf']) for token, v in s.auth.sessions.items() if v['expires'] > now])
            db.executemany("INSERT INTO notifications VALUES (?,?,?,?,?,?)", [(str(n.id), str(n.user.id), str(n.event.id), n.description, n.kind, int(n.read)) for n in s.notifications.notifications if n.event.id in valid])
            db.executemany("INSERT INTO subscriptions VALUES (?,?)", [(str(u), str(e)) for u, e in s.notifications.subscriptions if e in valid])
            db.executemany("INSERT INTO delivered VALUES (?,?,?)", [(str(u), str(e), date) for u, e, date in s.notifications.delivered if e in valid])
            db.executemany("INSERT INTO comments VALUES (?,?,?,?)", [(str(c.id), str(e), str(c.author.id), c.text) for e, comments in s.comments.comments.items() if e in valid for c in comments])
            if audit:
                db.execute("INSERT INTO audit(date,actor,action,detail) VALUES (?,?,?,?)", (minsk_now().isoformat(timespec='seconds'), *audit))

    def recentLog(self):
        """Возвращает последние сто действий для панели администратора."""
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")]
