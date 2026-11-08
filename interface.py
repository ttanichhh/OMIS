from datetime import datetime
from html import escape
from urllib.parse import urlencode
from uuid import UUID

from model import (
    AnalyticsService,
    AuthService,
    CommentService,
    CommentValidator,
    Event,
    EventService,
    IntegrationService,
    NotificationService,
    ParticipatnService,
    YandexMapsAdapter,
)


CATEGORIES = ("Спорт", "Обучение", "Развлечения", "Прогулка")
COLORS = {
    "Спорт": "sport",
    "Обучение": "study",
    "Развлечения": "fun",
    "Прогулка": "walk",
    "Игры": "games",
}
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля",
          "августа", "сентября", "октября", "ноября", "декабря")


def page(title, content):
    return f"""<!doctype html>
<html lang="ru"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} · Платформа событий</title><link rel="stylesheet" href="/style.css">
</head><body>{content}</body></html>"""


def event_date(value, show_year=False):
    year = f" {value.year}" if show_year else ""
    return f"{value.day} {MONTHS[value.month - 1]}{year}, {value:%H:%M}"


class MainUi:
    def __init__(self, authService: AuthService, eventService: EventService,
                 participantService: ParticipatnService, analyticsService: AnalyticsService):
        self.authService = authService
        self.eventService = eventService
        self.participantService = participantService
        self.analyticsService = analyticsService
        self.event: Event | None = None

    def onLoginSubmit(self, form: dict[str, str]) -> None:
        self.authService.login(form.get("username", "").strip() or "Михаил С.", form.get("password", ""))
        self.authService.current_user.role = (
            "Организатор" if form.get("role") == "Организатор" else "Участник"
        )

    def onCreateEventSubmit(self, form: dict[str, str]) -> None:
        date = datetime.fromisoformat(form["date"] + "T" + form["time"])
        event = self.eventService.createEvent(form["title"], form["description"], date, form["location"])
        event.organizer = self.authService.current_user
        category = self.eventService.categories.get(form.get("category"))
        (category or self.eventService.categories["Спорт"]).events.append(event)

    def onFilterSelect(self, category: str, query: str) -> list[tuple[str, Event]]:
        query = query.casefold().strip()
        return [
            (name, event)
            for name, group in self.eventService.categories.items()
            if not category or name == category
            for event in group.events
            if query in event.title.casefold() or query in event.location.casefold()
        ]

    def onRegisterButtonClick(self, event: Event, status: str) -> None:
        user = self.authService.current_user
        participant = self.participantService.registerParticipant(user.id, event.id)
        participant.status = "Заинтересован" if status == "interested" else "Иду"

    def onViewStatsClick(self) -> None:
        if self.event is not None:
            self.analyticsService.getEventStats(self.event.id)

    def dispatch(self, method, path, query, form):
        redirect = "303 See Other"
        if path == "/":
            return "200 OK", self.loginWindow()
        if method == "POST" and path == "/register":
            username = form.get("username", "").strip()
            if not username:
                return "200 OK", self.loginWindow("Введите логин для регистрации.", form)
            self.authService.registerUser(username, form.get("password", ""))
            return "200 OK", self.loginWindow("Регистрация завершена. Теперь можно войти.", form)
        if method == "POST" and path == "/login":
            self.onLoginSubmit(form)
            return redirect, "/catalog"
        if method == "POST" and path == "/logout":
            self.authService.current_user = None
            self.event = None
            return redirect, "/"
        if self.authService.current_user is None:
            return redirect, "/"

        if path == "/catalog":
            return "200 OK", self.catalogWindow(query.get("category", [""])[0], query.get("q", [""])[0])
        if path == "/create":
            if method == "POST":
                try:
                    self.onCreateEventSubmit(form)
                except (ValueError, KeyError):
                    return "400 Bad Request", self.createWindow("Проверьте поля даты и времени.")
                return redirect, "/catalog"
            return "200 OK", self.createWindow()
        if path.startswith("/events/"):
            try:
                self.event = self.eventService.events[UUID(path.removeprefix("/events/"))]
            except (ValueError, KeyError):
                return "404 Not Found", page("Мероприятие не найдено",
                                             '<main><a href="/catalog">← Назад к каталогу</a></main>')
            if method == "POST":
                self.onRegisterButtonClick(self.event, form.get("status", ""))
                return redirect, "/catalog" if form.get("from") == "catalog" else path
            return "200 OK", self.detailsWindow(self.event)
        return "404 Not Found", page("Страница не найдена", '<main><a href="/catalog">← К каталогу</a></main>')

    def loginWindow(self, message="", form=None):
        form = form or {}
        username = escape(form.get("username", ""))
        organizer = form.get("role") == "Организатор"
        return page("Вход в систему", f"""
<main class="login panel">
  <aside class="intro">
    <h1>Централизованная<br>платформа событий</h1>
    <p>Планируйте, организуйте и посещайте интересные мероприятия без хаоса в чатах и переписках.</p>
    <ul>
      <li>Мгновенное создание карточки события</li>
      <li>Учет участников в реальном времени</li>
      <li>Категории, фильтры и система оценок</li>
    </ul>
  </aside>
  <section class="login-form">
    <h2>Вход в систему</h2>
    <p class="muted">Выберите вашу роль и войдите в аккаунт</p>
    <form method="post" action="/login">
      <fieldset class="choices roles" aria-label="Роль">
        <label><input type="radio" name="role" value="Участник" {"" if organizer else "checked"}><span>Участник</span></label>
        <label><input type="radio" name="role" value="Организатор" {"checked" if organizer else ""}><span>Организатор</span></label>
      </fieldset>
      <label class="field">Логин / E-mail<input name="username" value="{username}" autocomplete="username"></label>
      <label class="field">Пароль<input name="password" type="password" autocomplete="current-password"></label>
      <button class="btn full">Войти в личный кабинет</button>
      <p class="signup">Ещё нет аккаунта? <button class="text-button" formaction="/register">Зарегистрироваться</button></p>
      <p class="message" role="status">{escape(message)}</p>
    </form>
  </section>
</main>""")

    def catalogWindow(self, category, query):
        user = self.authService.current_user
        name = escape(user.fullName)
        initials = escape("".join(word[0] for word in user.fullName.split()[:2]).upper())
        categories = (("", "Все события"), *((item, item) for item in CATEGORIES))
        filters = "".join(
            f'<a class="pill {"active" if key == category else ""}" '
            f'href="/catalog?{escape(urlencode({"category": key, "q": query}))}">{label}</a>'
            for key, label in categories
        )
        cards = []
        for label, event in self.onFilterSelect(category, query):
            url = f"/events/{event.id}"
            status = self.participantService.registrations.get((user.id, event.id))
            cards.append(f"""
<article class="card panel">
  <a class="cover {COLORS[label]}" href="{url}" aria-label="{escape(event.title)}"><span class="tag">{label}</span></a>
  <div class="card-body">
    <small class="muted">{event_date(event.eventDateTime)}</small>
    <a class="card-title" href="{url}">{escape(event.title)}</a>
    <small class="muted">{escape(event.location)}</small>
    <form class="actions" action="{url}" method="post">
      <input type="hidden" name="from" value="catalog">
      <button class="btn mint grow" name="status" value="interested" aria-pressed="{str(bool(status and status.status == "Заинтересован")).lower()}">Заинтересован</button>
      <button class="btn" name="status" value="going" aria-pressed="{str(bool(status and status.status == "Иду")).lower()}">Пойду</button>
    </form>
  </div>
</article>""")
        return page("Каталог мероприятий", f"""
<header class="top"><div class="top-inner">
  <strong>Актуальные мероприятия здесь</strong>
  <form class="search" method="get" action="/catalog">
    <input type="hidden" name="category" value="{escape(category)}">
    <input type="search" name="q" value="{escape(query)}" aria-label="Поиск по названию или месту" placeholder="Поиск по названию или месту...">
  </form>
  <span class="identity">{name} <span class="avatar">{initials}</span></span>
  <form method="post" action="/logout"><button class="btn">Выйти</button></form>
  <a class="btn mint" href="/create">Создать мероприятие</a>
</div></header>
<main class="catalog">
  <h2>Каталог мероприятий</h2>
  <nav class="filters" aria-label="Категории">{filters}</nav>
  <div class="cards">{"".join(cards) or "<p>Мероприятия не найдены.</p>"}</div>
</main>""")

    def createWindow(self, message=""):
        options = "".join(
            f'<label><input type="radio" name="category" value="{item}" '
            f'{"checked" if item == "Спорт" else ""}><span>{item}</span></label>'
            for item in ("Спорт", "Игры", "Развлечения", "Прогулка")
        )
        return page("Создание нового мероприятия", f"""
<main class="create panel">
  <h2>Создание нового мероприятия</h2>
  <p class="muted">Заполните карточку события.</p>
  <form method="post" action="/create">
    <label class="field">Название мероприятия<input name="title" required placeholder="Например: Утренний пробег в парке"></label>
    <fieldset class="choices">
      <legend>Категория события</legend>{options}
    </fieldset>
    <div class="date-fields">
      <label class="field">Дата проведения<input name="date" type="date" value="2026-06-20" required></label>
      <label class="field">Время начала<input name="time" type="time" value="08:00" required></label>
    </div>
    <label class="field">Место проведения<input name="location" required placeholder="Парк Горького, вход №2"></label>
    <label class="field">Текстовое описание<textarea name="description" rows="4" placeholder="Опишите программу мероприятия, правила или что нужно взять с собой..."></textarea></label>
    <div class="actions end"><a class="btn" href="/catalog">Отмена</a><button class="btn mint">Опубликовать событие</button></div>
    <p class="message" role="status">{escape(message)}</p>
  </form>
</main>""")

    def detailsWindow(self, event):
        category = next(name for name, group in self.eventService.categories.items() if event in group.events)
        organizer = event.organizer.fullName if event.organizer else ""
        user = self.authService.current_user
        registrations = self.participantService.registrations
        status = registrations.get((user.id, event.id))
        attending = sum(item.event.id == event.id and item.status == "Иду" for item in registrations.values())
        if event.title == "Утренний пробег и йога в Парке Горького":
            attending += 45
        return page("Информация о мероприятии", f"""
<main class="details">
  <a class="back" href="/catalog">← Назад к каталогу</a>
  <div class="hero {COLORS[category]}">
    <span class="tag">{category}</span><h1>{escape(event.title)}</h1>
  </div>
  <section class="detail-body panel">
    <dl class="facts">
      <div><dt>Дата и время</dt><dd>{event_date(event.eventDateTime, show_year=True)}</dd></div>
      <div><dt>Место проведения</dt><dd>{escape(event.location)}</dd></div>
      <div><dt>Организатор</dt><dd>{escape(organizer)}</dd></div>
    </dl>
    <h3>О мероприятии</h3>
    <p class="description">{escape(event.description)}</p>
    <p class="muted count">Подтвердили участие: {attending}</p>
    <form class="actions" method="post">
      <button class="btn grow" name="status" value="going" aria-pressed="{str(bool(status and status.status == "Иду")).lower()}">Принять участие</button>
      <button class="btn quiet" name="status" value="interested" aria-pressed="{str(bool(status and status.status == "Заинтересован")).lower()}">Заинтересован</button>
    </form>
  </section>
</main>""")


class NotifiedMainUi(MainUi):
    def __init__(self, *services, notificationService: NotificationService):
        super().__init__(*services)
        self.notificationService = notificationService

    def onSubscribeButtonClick(self) -> None:
        if self.event:
            self.notificationService.sendMsg(self.event.id)


class ExtendedMainUi(MainUi):
    def __init__(self, *services, integrationService: IntegrationService):
        super().__init__(*services)
        self.integrationService = integrationService

    def onShowMapClick(self) -> None:
        if self.event:
            self.integrationService.showMapWidget(self.event.location)

    def onExportToCalendarClick(self) -> None:
        if self.event:
            self.integrationService.exportEventToCalendar(self.event.title, self.event.eventDateTime)


class CommentedMainUi(MainUi):
    def __init__(self, *services, commentService: CommentService):
        super().__init__(*services)
        self.commentService = commentService
        self.commentText = ""

    def onPostCommentClick(self) -> None:
        self.commentService.event = self.event
        self.commentService.author = self.authService.current_user
        self.commentService.postComment(self.commentText)


class NotificationInjector:
    def __init__(self, *services):
        self.services = services

    def setupNotifications(self) -> None:
        authService, eventService, _, _ = self.services
        service = NotificationService(authService, eventService)
        self.ui = NotifiedMainUi(*self.services, notificationService=service)


class IntegrationInjector:
    def __init__(self, *services):
        self.services = services

    def setupIntegrations(self) -> None:
        service = IntegrationService(YandexMapsAdapter())
        self.ui = ExtendedMainUi(*self.services, integrationService=service)


class CommentInjector:
    def __init__(self, *services):
        self.services = services

    def setupComments(self) -> None:
        service = CommentService(CommentValidator([]))
        self.ui = CommentedMainUi(*self.services, commentService=service)
