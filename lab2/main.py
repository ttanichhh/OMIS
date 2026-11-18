from datetime import datetime
from hmac import compare_digest
from http.cookies import SimpleCookie, CookieError
import logging
from pathlib import Path
import re
import secrets
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server, WSGIServer

from interface import MainUi, page
from model import AuthService, EventService
from storage import StateStore

logger = logging.getLogger("events")


class LocalServer(WSGIServer):
    def get_request(self):
        """Ограничивает ожидание данных от открытого браузером соединения, чтобы не блокировать остальные запросы."""
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address


class MainApplication:
    @staticmethod
    def buildUi(services):
        """Создаёт базовый интерфейс из четырёх окон отчёта; расширения подключаются отдельно."""
        return MainUi(*services.base())

    @staticmethod
    def createApplication(database=None):
        """Собирает WSGI-приложение с постоянным хранилищем, сессиями и проверкой POST-запросов."""
        store = StateStore(database or Path(__file__).with_name("events.sqlite3"))
        initial = store.load()
        if not initial.auth.users and not initial.events.events:
            MainApplication.addExamples(initial.auth, initial.events)
            store.save(initial)
        css_path = Path(__file__).with_name("style.css")

        def application(environ, start_response):
            """Обрабатывает отдельный HTTP-запрос, атомарно сохраняет результат и выдаёт cookies браузеру."""
            path = environ.get("PATH_INFO", "/")
            if path == "/style.css":
                start_response("200 OK", [("Content-Type", "text/css; charset=utf-8")])
                return [css_path.read_bytes()]
            headers = [("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"),
                       ("Content-Security-Policy", "default-src 'self'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"),
                       ("Referrer-Policy", "same-origin")]
            method = environ.get("REQUEST_METHOD", "GET")
            actor = "Гость"
            try:
                cookies = SimpleCookie()
                try:
                    cookies.load(environ.get("HTTP_COOKIE", ""))
                except CookieError:
                    pass
                token = cookies['session'].value if 'session' in cookies else ''
                cookie_csrf = cookies['csrf'].value if 'csrf' in cookies else ''
                services = store.load()
                user = services.auth.restoreSession(token)
                actor = user.username if user else "Гость"
                session = services.auth.sessions.get(token) if user else None
                csrf = session['csrf'] if session else cookie_csrf or secrets.token_urlsafe(32)
                ui = MainApplication.buildUi(services)
                ui.session_token = token
                query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
                form = {}
                if method == "POST":
                    length = int(environ.get("CONTENT_LENGTH") or 0)
                    if length < 0 or length > 1_000_000:
                        start_response("413 Content Too Large", [("Content-Type", "text/html; charset=utf-8"), *headers])
                        return [page("Слишком большой запрос", '<main class="create panel">Уменьшите объём отправляемых данных.</main>').encode()]
                    raw = environ['wsgi.input'].read(length).decode('utf-8')
                    form = {key: values[0] for key, values in parse_qs(raw, keep_blank_values=True).items()}
                    submitted = form.get('csrf', '')
                    if not submitted or not compare_digest(submitted.encode(), csrf.encode()):
                        start_response("403 Forbidden", [("Content-Type", "text/html; charset=utf-8"), *headers])
                        return [ui.errorWindow("Обновите страницу", "Проверка формы не пройдена. Откройте страницу заново и повторите действие.").encode()]
                status, content = ui.dispatch(method, path, query, form)
                if ui.login_token:
                    if token:
                        services.auth.sessions.pop(token, None)
                    token = ui.login_token
                    csrf = services.auth.sessions[token]['csrf']
                    headers.append(("Set-Cookie", f"session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"))
                    actor = services.auth.current_user.username
                if path == "/logout" and method == "POST" and status.startswith("303"):
                    headers.append(("Set-Cookie", "session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"))
                    csrf = secrets.token_urlsafe(32)
                headers.append(("Set-Cookie", f"csrf={csrf}; Path=/; HttpOnly; SameSite=Strict"))
                audit = (actor, ui.action or f"{method} {path}", status)
                store.save(services, audit)
                logger.info("%s %s %s %s", actor, method, path, status)
                if status == "303 See Other":
                    headers.append(("Location", content))
                    content = ""
                elif ui.content_type.startswith("text/html"):
                    # Все POST-формы, включая формы расширений, получают защитный токен.
                    content = re.sub(r'(<form\b[^>]*method="post"[^>]*>)',
                                     lambda match: match.group(1) + f'<input type="hidden" name="csrf" value="{csrf}">', content)
                else:
                    headers.append(("Content-Disposition", 'attachment; filename="event.ics"'))
                start_response(status, [("Content-Type", ui.content_type), *headers])
                return [content.encode("utf-8")]
            except (ValueError, UnicodeDecodeError):
                start_response("400 Bad Request", [("Content-Type", "text/html; charset=utf-8"), *headers])
                return [page("Ошибка запроса", '<main class="create panel">Проверьте отправленные данные. <a href="/catalog">К каталогу</a></main>').encode()]
            except Exception:
                logger.exception("Ошибка запроса %s %s", method, path)
                start_response("500 Internal Server Error", [("Content-Type", "text/html; charset=utf-8"), *headers])
                return [page("Ошибка приложения", '<main class="create panel">Не удалось обработать запрос. Действие не сохранено. <a href="/catalog">К каталогу</a></main>').encode()]

        application.store = store
        return application

    @staticmethod
    def main(args=None):
        """Запускает локальный сервер с базовыми экранами отчёта."""
        logging.basicConfig(filename=Path(__file__).with_name("events.log"), level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s")
        application = MainApplication.createApplication()
        with make_server("127.0.0.1", 8000, application, server_class=LocalServer) as server:
            print("Откройте http://127.0.0.1:8000/. Остановка: Ctrl+C.", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass

    @staticmethod
    def addExamples(authService: AuthService, eventService: EventService):
        """Добавляет примеры из макетов в новую базу, не изменяя существующие данные."""
        organizer = authService.registerUser("ivan", secrets.token_urlsafe(32), "Организатор", "Иван П")
        authService.current_user = organizer
        examples = (
            ("Спорт", "Утренний пробег и йога в Парке Горького", datetime(2026, 6, 20, 8),
             "Парк Горького, Вход №2",
             "Приглашаем всех любителей активного отдыха на совместный утренний забег на 5 км с последующей сессией растяжки и йоги. Подходит для любого уровня подготовки. После тренировки — чаепитие и знакомство с участниками. С собой иметь удобную спортивную форму и коврик для йоги."),
            ("Обучение", "Лекция: Архитектурные паттерны", datetime(2026, 6, 25, 19),
             "Технопарк, Конференц-зал В", "Лекция об архитектурных паттернах."),
            ("Развлечения", "Киновечер под открытым небом", datetime(2026, 6, 28, 20, 30),
             "Крыша пространства «Север»", "Вечерний показ фильма под открытым небом."),
        )
        try:
            for category, title, date, location, description in examples:
                eventService.createEvent(title, description, date, location, category)
        finally:
            authService.current_user = None



if __name__ == "__main__":
    MainApplication.main()
