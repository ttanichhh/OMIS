from datetime import datetime
from html import escape
from urllib.parse import urlencode
from uuid import UUID

from model import (AnalyticsService, AuthService, CATEGORIES, ROLES, CommentService,
                   CommentValidator, Event, EventService, IntegrationService,
                   NotificationService, ParticipatnService, YandexMapsAdapter)

COLORS = dict(zip(CATEGORIES, ("sport", "study", "games", "fun", "walk")))
MONTHS = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")


def page(title, content):
    """Создаёт HTML-документ с русским языком, заголовком и общими стилями."""
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(title)} · Платформа событий</title><link rel="stylesheet" href="/style.css"></head><body>{content}</body></html>'''


def event_date(value, show_year=False):
    """Выводит местные дату и время Минска с русским названием месяца."""
    return f'{value.day} {MONTHS[value.month - 1]}{f" {value.year}" if show_year else ""}, {value:%H:%M}'


CATALOG_CATEGORIES = ("Спорт", "Обучение", "Развлечения", "Прогулка")
CREATE_CATEGORIES = ("Спорт", "Игры", "Развлечения", "Прогулка")


def form_date(form):
    """Преобразует дату формы и выдаёт понятное сообщение при неверном формате."""
    try:
        return datetime.fromisoformat(form.get("date", "") + "T" + form.get("time", ""))
    except ValueError:
        raise ValueError("Укажите корректные дату и время проведения.") from None


class MainUi:
    def __init__(self, authService: AuthService, eventService: EventService,
                 participantService: ParticipatnService, analyticsService: AnalyticsService):
        """Подключает основные сервисы и создаёт контекст одного запроса."""
        self.authService, self.eventService = authService, eventService
        self.participantService, self.analyticsService = participantService, analyticsService
        self.event: Event | None = None
        self.session_token = ""
        self.login_token = None
        self.content_type = "text/html; charset=utf-8"
        self.action = ""

    def onLoginSubmit(self, form):
        """Проверяет логин и пароль; роль существующего аккаунта не изменяется."""
        self.login_token = self.authService.login(form.get("username", ""), form.get("password", ""))

    def onCreateEventSubmit(self, form):
        """Создаёт событие из полей формы, связывая с текущим организатором и категорией."""
        if form.get("category") not in CREATE_CATEGORIES:
            raise ValueError("Выберите категорию из формы создания.")
        self.event = self.eventService.createEvent(form.get("title", ""), form.get("description", ""),
            form_date(form), form.get("location", ""), form["category"])
        return self.event

    def onFilterSelect(self, category, query):
        """Фильтрует каталог по категории, названию и месту проведения."""
        query = query.casefold().strip()
        return [(name, event) for name, group in self.eventService.categories.items()
                if not category or name == category for event in group.events
                if query in event.title.casefold() or query in event.location.casefold()]

    def onRegisterButtonClick(self, event, status):
        """Устанавливает один из двух статусов участия, показанных в макетах."""
        user = self.authService.requireRole(*ROLES)
        if status not in ("going", "interested"):
            raise ValueError("Выберите статус участия.")
        registration = self.participantService.registerParticipant(user.id, event.id)
        registration.status = "Иду" if status == "going" else "Заинтересован"

    def onViewStatsClick(self):
        """Возвращает статистику выбранного события через метод базовой модели."""
        if self.event:
            return self.analyticsService.getEventStats(self.event.id)

    def dispatch(self, method, path, query, form):
        """Обрабатывает четыре окна и переходы отчёта; дополнительные маршруты отсутствуют."""
        try:
            return self.route(method, path, query, form)
        except PermissionError as error:
            return "403 Forbidden", self.errorWindow("Доступ запрещён", str(error))
        except (ValueError, KeyError) as error:
            message = str(error) if isinstance(error, ValueError) else "Проверьте заполнение полей."
            if path in ("/login", "/register"):
                return "400 Bad Request", self.loginWindow(message, form)
            if path == "/create":
                return "400 Bad Request", self.createWindow(message, form)
            return "400 Bad Request", self.errorWindow("Ошибка", message)

    def route(self, method, path, query, form):
        """Открывает вход, каталог, создание и детали, выполняет регистрацию, вход, выход и запись."""
        if method not in ("GET", "POST"):
            return "405 Method Not Allowed", self.errorWindow("Ошибка", "Метод запроса не поддерживается.")
        if path == "/" and method == "GET":
            return "200 OK", self.loginWindow()
        if path == "/register" and method == "POST":
            role = form.get("role", "Участник")
            if role not in ROLES[:2]:
                raise PermissionError("Выберите роль из формы входа.")
            self.authService.registerUser(form.get("username", ""), form.get("password", ""), role)
            self.action = "Регистрация"
            return "200 OK", self.loginWindow("Регистрация завершена. Теперь можно войти.", form)
        if path == "/login" and method == "POST":
            self.onLoginSubmit(form)
            self.action = "Вход"
            return "303 See Other", "/catalog"
        if path == "/logout" and method == "POST":
            self.authService.sessions.pop(self.session_token, None)
            self.authService.current_user = None
            self.action = "Выход"
            return "303 See Other", "/"
        # В отчёте маршруты новых экранов не определены, поэтому они недоступны и по прямому URL.
        if path not in ("/catalog", "/create") and not (path.startswith("/events/") and len(path.strip("/").split("/")) == 2):
            return "404 Not Found", self.errorWindow("Страница не найдена", "Вернитесь в каталог.")
        if self.authService.current_user is None:
            return "303 See Other", "/"
        if path == "/catalog" and method == "GET":
            return "200 OK", self.catalogWindow(query.get("category", [""])[0], query.get("q", [""])[0])
        if path == "/create":
            self.eventService.requireOwner()
            if method == "POST":
                self.onCreateEventSubmit(form)
                self.action = "Создано мероприятие"
                return "303 See Other", "/catalog"
            return "200 OK", self.createWindow()
        if path.startswith("/events/"):
            try:
                self.event = self.eventService.events[UUID(path.removeprefix("/events/"))]
            except (ValueError, KeyError):
                return "404 Not Found", self.errorWindow("Мероприятие не найдено", "Вернитесь в каталог.")
            if method == "POST":
                self.onRegisterButtonClick(self.event, form.get("status", ""))
                self.action = "Изменён статус участия"
                return "303 See Other", "/catalog" if form.get("from") == "catalog" else path
            self.analyticsService.recordView(self.event.id)
            return "200 OK", self.detailsWindow(self.event)
        return "404 Not Found", self.errorWindow("Страница не найдена", "Вернитесь в каталог.")

    def errorWindow(self, title, message):
        """Показывает ошибку с возвратом к предусмотренным экранам приложения."""
        return page(title, f'<main class="create panel"><h2>{escape(title)}</h2><p>{escape(message)}</p><a href="/catalog">← К каталогу</a></main>')

    def loginWindow(self, message="", form=None):
        """Выводит окно входа из рисунка 1: две роли, логин, пароль, вход и регистрация."""
        form = form or {}
        organizer = form.get("role") == "Организатор"
        return page("Вход в систему", f'''<main class="login panel"><aside class="intro">
<h1>Централизованная<br>платформа событий</h1>
<p>Планируйте, организуйте и посещайте интересные мероприятия без хаоса в чатах и переписках.</p>
<ul><li>Мгновенное создание карточки события</li><li>Учет участников в реальном времени</li><li>Категории, фильтры и система оценок</li></ul>
</aside><section class="login-form"><h2>Вход в систему</h2><p class="muted">Выберите вашу роль и войдите в аккаунт</p>
<form method="post" action="/login"><fieldset class="choices roles" aria-label="Роль">
<label><input type="radio" name="role" value="Участник" {"" if organizer else "checked"}><span>Участник</span></label>
<label><input type="radio" name="role" value="Организатор" {"checked" if organizer else ""}><span>Организатор</span></label></fieldset>
<label class="field">Логин / E-mail<input name="username" value="{escape(form.get('username', ''))}" autocomplete="username" required></label>
<label class="field">Пароль<input name="password" type="password" autocomplete="current-password" required></label>
<button class="btn full">Войти в личный кабинет</button>
<p class="signup">Ещё нет аккаунта? <button class="text-button" formaction="/register">Зарегистрироваться</button></p>
<p class="message" role="status">{escape(message)}</p></form></section></main>''')

    def participationForm(self, event, source="details"):
        """Показывает ровно две кнопки статусов из соответствующего макета."""
        registration = self.participantService.registrations.get((self.authService.current_user.id, event.id))
        status = registration.status if registration else ""
        going = f'<button class="btn {"grow" if source == "details" else ""}" name="status" value="going" aria-pressed="{str(status == "Иду").lower()}">{"Принять участие" if source == "details" else "Пойду"}</button>'
        interested = f'<button class="btn {"quiet" if source == "details" else "mint grow"}" name="status" value="interested" aria-pressed="{str(status == "Заинтересован").lower()}">Заинтересован</button>'
        buttons = going + interested if source == "details" else interested + going
        return f'<form class="actions" method="post" action="/events/{event.id}"><input type="hidden" name="from" value="{source}">{buttons}</form>'

    def catalogWindow(self, category, query):
        """Выводит каталог из рисунка 2 с поиском, фильтрами и двумя кнопками на карточке."""
        user = self.authService.current_user
        initials = escape("".join(word[0] for word in user.fullName.split()[:2]).upper())
        create_button = '<a class="btn mint" href="/create">Создать мероприятие</a>' if user.role in ("Организатор", "Администратор") else ''
        filters = ''.join(f'<a class="pill {"active" if key == category else ""}" href="/catalog?{escape(urlencode({"category":key,"q":query}))}">{label}</a>' for key,label in (("", "Все события"), *((c,c) for c in CATALOG_CATEGORIES)))
        cards = ''.join(f'''<article class="card panel"><a class="cover {COLORS[label]}" href="/events/{event.id}" aria-label="{escape(event.title)}"><span class="tag">{label}</span></a>
<div class="card-body"><small class="muted">{event_date(event.eventDateTime)}</small><a class="card-title" href="/events/{event.id}">{escape(event.title)}</a>
<small class="muted">{escape(event.location)}</small>{self.participationForm(event, 'catalog')}</div></article>''' for label,event in self.onFilterSelect(category,query))
        return page("Каталог мероприятий", f'''<header class="top"><div class="top-inner"><strong>Актуальные мероприятия здесь</strong>
<form class="search" method="get" action="/catalog"><input type="hidden" name="category" value="{escape(category)}"><input type="search" name="q" value="{escape(query)}" aria-label="Поиск по названию или месту" placeholder="Поиск по названию или месту..."></form>
<span class="identity">{escape(user.fullName)} <span class="avatar">{initials}</span></span>
<form method="post" action="/logout"><button class="btn">Выйти</button></form>{create_button}</div></header>
<main class="catalog"><h2>Каталог мероприятий</h2><nav class="filters">{filters}</nav><div class="cards">{cards or '<p>Мероприятия не найдены.</p>'}</div></main>''')

    def createWindow(self, message="", form=None):
        """Показывает поля и категории формы создания из рисунка 3, сохраняя ввод при ошибке."""
        form = form if form is not None else {"date":"2026-06-20", "time":"08:00", "category":"Спорт"}
        options = ''.join(f'<label><input type="radio" name="category" value="{item}" {"checked" if item == form.get("category") else ""}><span>{item}</span></label>' for item in CREATE_CATEGORIES)
        return page("Создание нового мероприятия", f'''<main class="create panel"><h2>Создание нового мероприятия</h2><p class="muted">Заполните карточку события.</p>
<form method="post" action="/create"><label class="field">Название мероприятия<input name="title" required value="{escape(form.get('title',''))}" placeholder="Например: Утренний пробег в парке"></label>
<fieldset class="choices"><legend>Категория события</legend>{options}</fieldset><div class="date-fields">
<label class="field">Дата проведения<input name="date" type="date" required value="{escape(form.get('date',''))}"></label>
<label class="field">Время начала<input name="time" type="time" required value="{escape(form.get('time',''))}"></label></div>
<label class="field">Место проведения<input name="location" required value="{escape(form.get('location',''))}" placeholder="Парк Горького, вход №2"></label>
<label class="field">Текстовое описание<textarea name="description" rows="4" placeholder="Опишите программу мероприятия, правила или что нужно взять с собой...">{escape(form.get('description',''))}</textarea></label>
<div class="actions end"><a class="btn" href="/catalog">Отмена</a><button class="btn mint">Опубликовать событие</button></div>
<p class="message" role="status">{escape(message)}</p></form></main>''')

    def detailsWindow(self, event):
        """Выводит рисунок 4: сведения о событии, участников и две кнопки участия."""
        category = self.eventService.categoryFor(event)
        registrations = [r for r in self.participantService.registrations.values() if r.event.id == event.id and r.status == "Иду"]
        avatars = ''.join(f'<span class="avatar" title="{escape(r.user.fullName)}">{escape(r.user.fullName[:1].upper())}</span>' for r in registrations[:3])
        if len(registrations) > 3:
            avatars += f'<span class="avatar">+{len(registrations)-3}</span>'
        return page("Информация о мероприятии", f'''<main class="details"><a class="back" href="/catalog">← Назад к каталогу</a>
<div class="hero {COLORS[category]}"><span class="tag">{category}</span><h1>{escape(event.title)}</h1></div>
<section class="detail-body panel"><dl class="facts"><div><dt>Дата и время</dt><dd>{event_date(event.eventDateTime, True)}</dd></div>
<div><dt>Место проведения</dt><dd>{escape(event.location)}</dd></div><div><dt>Организатор</dt><dd>{escape(event.organizer.fullName if event.organizer else '')}</dd></div></dl>
<h3>О мероприятии</h3><p class="description">{escape(event.description)}</p>
<div class="attendees">{avatars}<span class="muted">{len(registrations)} человек уже подтвердили участие</span></div>
{self.participationForm(event)}</section></main>''')


class NotifiedMainUi(MainUi):
    def __init__(self, *services, notificationService: NotificationService):
        """Подключает сервис уведомлений к отдельному расширению базового интерфейса."""
        super().__init__(*services)
        self.notificationService = notificationService

    def onSubscribeButtonClick(self):
        """Вызывает sendMsg для выбранного события, как предусмотрено диаграммой расширения."""
        if self.event:
            return self.notificationService.sendMsg(self.event.id)


class ExtendedMainUi(MainUi):
    def __init__(self, *services, integrationService: IntegrationService):
        """Подключает карты и календарь к отдельному расширению интерфейса."""
        super().__init__(*services)
        self.integrationService = integrationService

    def onShowMapClick(self):
        """Получает ссылку на карту выбранного мероприятия через сервис интеграций."""
        if self.event:
            return self.integrationService.showMapWidget(self.event.location)

    def onExportToCalendarClick(self):
        """Вызывает экспорт выбранного мероприятия через метод диаграммы расширения."""
        if self.event:
            return self.integrationService.exportEventToCalendar(self.event.title, self.event.eventDateTime)


class CommentedMainUi(MainUi):
    def __init__(self, *services, commentService: CommentService):
        """Подключает сервис комментариев к отдельному расширению интерфейса."""
        super().__init__(*services)
        self.commentService = commentService
        self.commentText = ""

    def onPostCommentClick(self):
        """Передаёт сервису автора, выбранное событие и введённый текст комментария."""
        self.commentService.event = self.event
        self.commentService.author = self.authService.current_user
        return self.commentService.postComment(self.commentText)


class NotificationInjector:
    def __init__(self, *services):
        """Сохраняет основные сервисы для создания расширения уведомлений."""
        self.services = services

    def setupNotifications(self):
        """Создаёт NotificationService и NotifiedMainUi по диаграмме расширения."""
        self.service = NotificationService(self.services[0], self.services[1])
        self.ui = NotifiedMainUi(*self.services, notificationService=self.service)
        return self.ui


class IntegrationInjector:
    def __init__(self, *services):
        """Сохраняет основные сервисы для создания расширения интеграций."""
        self.services = services

    def setupIntegrations(self):
        """Создаёт адаптер карт, IntegrationService и ExtendedMainUi по диаграмме."""
        self.service = IntegrationService(YandexMapsAdapter())
        self.ui = ExtendedMainUi(*self.services, integrationService=self.service)
        return self.ui


class CommentInjector:
    def __init__(self, *services):
        """Сохраняет основные сервисы для создания расширения комментариев."""
        self.services = services

    def setupComments(self):
        """Создаёт валидатор, CommentService и CommentedMainUi по диаграмме расширения."""
        self.service = CommentService(CommentValidator([]))
        self.ui = CommentedMainUi(*self.services, commentService=self.service)
        return self.ui
