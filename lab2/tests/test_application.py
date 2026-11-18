"""Сценарии четырёх окон отчёта и операции его объектной модели."""
from datetime import datetime
from http.cookies import SimpleCookie
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from urllib.parse import urlencode

from interface import MainUi, NotificationInjector, IntegrationInjector, CommentInjector
from main import MainApplication
from model import CommentValidator


class Client:
    def __init__(self, app):
        """Создаёт отдельный браузер с собственными cookies."""
        self.app, self.cookies, self.body, self.headers = app, SimpleCookie(), '', []

    def request(self, path, method='GET', form=None, csrf=True):
        """Отправляет запрос к WSGI, сохраняет cookies и возвращает числовой статус."""
        form = dict(form or {})
        if method == 'POST' and csrf:
            if 'csrf' not in self.cookies:
                self.request('/')
            form.setdefault('csrf', self.cookies['csrf'].value)
        raw = urlencode(form).encode()
        url, _, query = path.partition('?')
        environ = {'PATH_INFO':url, 'QUERY_STRING':query, 'REQUEST_METHOD':method,
                   'CONTENT_LENGTH':str(len(raw)), 'wsgi.input':BytesIO(raw),
                   'HTTP_COOKIE':self.cookies.output(header='',sep=';').strip()}
        response = {}
        def start(status, headers):
            """Запоминает статус и cookies ответа."""
            response['status'] = int(status.split()[0])
            self.headers = headers
            for key,value in headers:
                if key.lower() == 'set-cookie':
                    self.cookies.load(value)
        self.body = b''.join(self.app(environ,start)).decode()
        return response['status']

    def login(self, username, password='password123', role='Участник'):
        """Входит в существующий аккаунт без изменения его роли."""
        return self.request('/login','POST',{'username':username,'password':password,'role':role})


class ReportTests(unittest.TestCase):
    def setUp(self):
        """Подготавливает временную базу, организатора и участника с независимыми сессиями."""
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name)/'test.sqlite3'
        self.app = MainApplication.createApplication(self.db)
        self.owner, self.member = Client(self.app), Client(self.app)
        for client,name,role in ((self.owner,'owner','Организатор'),(self.member,'member','Участник')):
            self.assertEqual(client.request('/register','POST',{'username':name,'password':'password123','role':role}),200)
            self.assertEqual(client.login(name,role=role),303)
        self.form = {'title':'Проверка','description':'Описание','location':'Минск','category':'Спорт','date':'2026-06-20','time':'08:00'}
        self.assertEqual(self.owner.request('/create','POST',self.form),303)
        self.event = next(e for e in self.app.store.load().events.events.values() if e.title == 'Проверка')
        self.url = '/events/'+str(self.event.id)

    def tearDown(self):
        """Удаляет временную базу теста."""
        self.temp.cleanup()

    def test_login_matches_report(self):
        """На входе только две роли, логин и пароль; дополнительной настройки и поля имени нет."""
        guest = Client(self.app)
        self.assertEqual(guest.request('/'),200)
        for text in ('Вход в систему','name="username"','name="password"','value="Участник"','value="Организатор"','Зарегистрироваться'):
            self.assertIn(text,guest.body)
        for text in ('Создание администратора','fullName','Роль при регистрации','Администрирование'):
            self.assertNotIn(text,guest.body)
        self.assertEqual(guest.login('member','wrong'),400)
        self.assertEqual(guest.login('unknown'),400)
        self.assertEqual(guest.request('/register','POST',{'username':'member','password':'password123'}),400)

    def test_roles_and_sessions(self):
        """Права сохраняются в аккаунте; выход одного браузера не влияет на другой."""
        self.assertEqual(self.member.login('member',role='Организатор'),303)
        self.assertEqual(self.member.request('/create'),403)
        self.assertEqual(self.member.request('/create','POST',self.form),403)
        self.member.request('/catalog')
        self.assertNotIn('href="/create"',self.member.body)
        self.owner.request('/catalog')
        self.assertIn('href="/create"',self.owner.body)
        self.assertEqual(self.member.request('/logout','POST'),303)
        self.assertEqual(self.member.request('/catalog'),303)
        self.assertEqual(self.owner.request('/catalog'),200)

    def test_catalog_matches_report(self):
        """Каталог содержит поиск и категории макета, по две кнопки статуса, без добавленных разделов."""
        self.member.request('/catalog')
        for text in ('Актуальные мероприятия здесь','Каталог мероприятий','Обучение','Пойду','Заинтересован'):
            self.assertIn(text,self.member.body)
        for text in ('>Игры<','<select','Уведомления','Администрирование','Ваш статус','Отменить участие','Найти</button>'):
            self.assertNotIn(text,self.member.body)
        self.member.request('/catalog?'+urlencode({'q':'Проверка','category':'Спорт'}))
        self.assertIn('Проверка',self.member.body)
        self.assertNotIn('Киновечер',self.member.body)

    def test_create_matches_report_and_validation(self):
        """В форме категории и начальные значения макета, публикация возвращает в каталог."""
        self.owner.request('/create')
        for text in ('value="Игры"','value="2026-06-20"','value="08:00"','Отмена','Опубликовать событие','Текстовое описание'):
            self.assertIn(text,self.owner.body)
        self.assertNotIn('value="Обучение"',self.owner.body)
        self.assertNotIn('<header',self.owner.body)
        before = len(self.app.store.load().events.events)
        for changes in ({'title':' '},{'location':''},{'date':'bad'},{'category':'Обучение'}):
            self.assertEqual(self.owner.request('/create','POST',{**self.form,**changes}),400)
        self.assertEqual(len(self.app.store.load().events.events),before)
        self.assertEqual(self.owner.request('/create','POST',self.form),303)
        self.assertEqual(dict(self.owner.headers)['Location'],'/catalog')

    def test_details_has_only_report_actions(self):
        """Карточка имеет две кнопки макета и возврат; дополнительные блоки отсутствуют для всех ролей."""
        for client in (self.owner,self.member):
            self.assertEqual(client.request(self.url),200)
            for text in ('Дата и время','Место проведения','Организатор','О мероприятии','Принять участие','Заинтересован','Назад к каталогу'):
                self.assertIn(text,client.body)
            self.assertEqual(client.body.count('<button '),2)
            for text in ('Открыть карту','В календарь','Напомнить','Редактировать','Участники и приглашения','Статистика','Обсуждение','Удалить мероприятие','<header','Ваш статус'):
                self.assertNotIn(text,client.body)

    def test_extra_routes_removed(self):
        """Придуманные страницы и действия недоступны также по прямым адресам."""
        for path in ('/setup','/notifications','/admin',*(self.url+'/'+action for action in ('edit','stats','participants','map','calendar','subscribe','unsubscribe','invite','comment','comment-delete','delete'))):
            for method in ('GET','POST'):
                self.assertEqual(self.owner.request(path,method,{'username':'member'}),404,(path,method))
        self.assertEqual(self.member.request(self.url,'POST',{'status':'cancel'}),400)

    def test_participation_and_persistence(self):
        """Запись и смена статуса работают, не дублируются и сохраняются после перезапуска."""
        for _ in range(2):
            self.assertEqual(self.member.request(self.url,'POST',{'status':'going'}),303)
        self.assertEqual(len(self.app.store.load().participants.registrations),1)
        self.member.request(self.url)
        self.assertIn('1 человек уже подтвердили участие',self.member.body)
        self.assertEqual(self.member.request(self.url,'POST',{'status':'interested','from':'catalog'}),303)
        self.assertEqual(dict(self.member.headers)['Location'],'/catalog')
        self.member.app = MainApplication.createApplication(self.db)
        self.assertEqual(self.member.request(self.url),200)
        self.assertIn('aria-pressed="true"',self.member.body)
        self.assertEqual(next(iter(self.app.store.load().participants.registrations.values())).status,'Заинтересован')

    def test_base_model_operations_retained(self):
        """Операции редактирования, статистики и отмены из диаграммы сохранены в сервисах."""
        self.member.request(self.url,'POST',{'status':'going'})
        s = self.app.store.load()
        self.assertIs(type(MainApplication.buildUi(s)),MainUi)
        s.auth.current_user = s.auth.users['owner']
        self.assertTrue(s.events.updateEventDetails(self.event.id,datetime(2026,7,1,9),'Новое место'))
        self.assertEqual(s.analytics.getEventStats(self.event.id).viewsCount,0)
        s.auth.current_user = s.auth.users['member']
        registration = next(iter(s.participants.registrations.values()))
        self.assertTrue(s.participants.cancelRegistration(registration.id))
        with self.assertRaises(PermissionError):
            s.events.updateEventDetails(self.event.id,datetime(2026,7,1),'Обход')

    def test_extensions_remain_separate(self):
        """Инжекторы создают отдельные расширения диаграмм без расписания и дополнительных страниц."""
        s = self.app.store.load(); s.auth.current_user = s.auth.users['member']
        notification = NotificationInjector(*s.base()).setupNotifications()
        notification.event = s.events.events[self.event.id]
        self.assertIn('Напоминание о мероприятии',notification.onSubscribeButtonClick().description)
        self.assertFalse(hasattr(notification.notificationService,'deliverDue'))
        self.assertFalse(hasattr(notification.notificationService,'invite'))
        integration = IntegrationInjector(*s.base()).setupIntegrations()
        integration.event = notification.event
        self.assertTrue(integration.onShowMapClick().startswith('https://yandex.ru/maps/'))
        self.assertTrue(integration.onExportToCalendarClick())
        comments = CommentInjector(*s.base()).setupComments()
        comments.event, comments.commentText = notification.event, 'Комментарий'
        comments.onPostCommentClick()
        self.assertEqual(comments.commentService.getCommentsForEv1nt()[0].text,'Комментарий')
        with self.assertRaises(ValueError):
            CommentValidator([]).validateText(' ')
        with self.assertRaises(ValueError):
            CommentValidator(['слово']).validateText('СЛОВО')

    def test_security_and_legacy_database_retained(self):
        """Сохраняются защита форм, хеши паролей, схема и данные; старые аккаунты не требуют новой настройки."""
        self.assertEqual(self.member.request(self.url,'POST',{'status':'going'},csrf=False),403)
        s = self.app.store.load()
        self.assertNotIn('password123',s.auth.users['member'].password)
        with self.app.store.connect() as db:
            self.assertFalse(list(db.execute('PRAGMA foreign_key_check')))
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
        self.member.app = MainApplication.createApplication(self.db)
        self.assertEqual(self.member.request('/'),200)
        self.assertNotIn('Создание администратора',self.member.body)


if __name__ == '__main__':
    unittest.main()
