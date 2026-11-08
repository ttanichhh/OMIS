from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from interface import MainUi
from model import AnalyticsService, AuthService, EventService, ParticipatnService


class MainApplication:
    @staticmethod
    def main(args: list[str] | None = None) -> None:
        authService = AuthService()
        eventService = EventService()
        participantService = ParticipatnService(authService, eventService)
        analyticsService = AnalyticsService(eventService)
        ui = MainUi(authService, eventService, participantService, analyticsService)
        MainApplication.addExamples(authService, eventService)
        css = Path(__file__).with_name("style.css").read_bytes()

        def application(environ, start_response):
            path = environ["PATH_INFO"]
            if path == "/style.css":
                start_response("200 OK", [("Content-Type", "text/css; charset=utf-8")])
                return [css]

            method = environ["REQUEST_METHOD"]
            query = parse_qs(environ["QUERY_STRING"], keep_blank_values=True)
            form = {}
            if method == "POST":
                length = int(environ.get("CONTENT_LENGTH") or 0)
                data = environ["wsgi.input"].read(length).decode("utf-8")
                form = {key: values[0] for key, values in parse_qs(data, keep_blank_values=True).items()}

            status, content = ui.dispatch(method, path, query, form)
            headers = [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store")]
            if status == "303 See Other":
                headers.append(("Location", content))
                content = ""
            start_response(status, headers)
            return [content.encode("utf-8")]

        with make_server("127.0.0.1", 8000, application) as server:
            print("Откройте http://127.0.0.1:8000/ в браузере. Для остановки нажмите Ctrl+C.", flush=True)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass

    @staticmethod
    def addExamples(authService: AuthService, eventService: EventService) -> None:
        organizer = authService.registerUser("ivan", "")
        organizer.fullName = "Иван П"
        organizer.role = "Организатор"
        examples = (
            ("Спорт", "Утренний пробег и йога в Парке Горького", datetime(2026, 6, 20, 8),
             "Парк Горького, вход №2",
             "Приглашаем на утренний забег на 5 км, растяжку и йогу. Подходит для любого уровня подготовки. Возьмите спортивную форму и коврик."),
            ("Обучение", "Лекция: Архитектурные паттерны", datetime(2026, 6, 25, 19),
             "Технопарк, Конференц-зал В", "Лекция об архитектурных паттернах."),
            ("Развлечения", "Киновечер под открытым небом", datetime(2026, 6, 28, 20, 30),
             "Крыша пространства «Север»", "Вечерний показ фильма под открытым небом."),
        )
        for category, title, date, location, description in examples:
            event = eventService.createEvent(title, description, date, location)
            event.organizer = organizer
            eventService.categories[category].events.append(event)


if __name__ == "__main__":
    MainApplication.main()
