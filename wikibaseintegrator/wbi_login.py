import time
import webbrowser

import requests
from mwoauth import ConsumerToken, Handshaker
from requests_oauthlib import OAuth1

from wikibaseintegrator.wbi_backoff import wbi_backoff
from wikibaseintegrator.wbi_config import config

"""
Login class for Wikidata. Takes username and password and stores the session cookies and edit tokens.
"""


class Login(object):
    """
    A class which handles the login to Wikidata and the generation of edit-tokens
    """

    @wbi_backoff()
    def __init__(self, user=None, pwd=None, mediawiki_api_url=None, mediawiki_index_url=None, token_renew_period=1800,
                 use_clientlogin=False, consumer_key=None, consumer_secret=None, callback_url='oob', user_agent=None,
                 debug=False):
        """
        This class handles several types of login procedures. Either use user and pwd authentication or OAuth.
        Wikidata clientlogin can also be used. If using one method, do NOT pass parameters for another method.
        :param user: the username which should be used for the login
        :type user: str
        :param pwd: the password which should be used for the login
        :type pwd: str
        :param token_renew_period: Seconds after which a new token should be requested from the Wikidata server
        :type token_renew_period: int
        :param use_clientlogin: use authmanager based login method instead of standard login.
            For 3rd party data consumer, e.g. web clients
        :type use_clientlogin: bool
        :param consumer_key: The consumer key for OAuth
        :type consumer_key: str
        :param consumer_secret: The consumer secret for OAuth
        :type consumer_secret: str
        :param callback_url: URL which should be used as the callback URL
        :type callback_url: str
        :param user_agent: UA string to use for API requests.
        :type user_agent: str
        :return: None
        """

        self.mediawiki_api_url = config['MEDIAWIKI_API_URL'] if mediawiki_api_url is None else mediawiki_api_url
        self.mediawiki_index_url = config['MEDIAWIKI_INDEX_URL'] if mediawiki_index_url is None else mediawiki_index_url

        if debug:
            print(self.mediawiki_api_url)

        self.s = requests.Session()
        self.edit_token = ''
        self.instantiation_time = time.time()
        self.token_renew_period = token_renew_period

        self.consumer_key = consumer_key
        self.consumer_secret = consumer_secret
        self.response_qs = None
        self.callback_url = callback_url

        if user_agent:
            self.user_agent = user_agent
        else:
            # if a user is given append " (User:USER)" to the UA string and update that value in CONFIG
            if user and user.casefold() not in config['USER_AGENT_DEFAULT'].casefold():
                config['USER_AGENT_DEFAULT'] += " (User:{})".format(user)
            self.user_agent = config['USER_AGENT_DEFAULT']
        self.s.headers.update({
            'User-Agent': self.user_agent
        })

        if self.consumer_key and self.consumer_secret:
            # Oauth procedure, based on https://www.mediawiki.org/wiki/OAuth/For_Developers

            # Consruct a "consumer" from the key/secret provided by MediaWiki
            self.consumer_token = ConsumerToken(self.consumer_key, self.consumer_secret)

            # Construct handshaker with wiki URI and consumer
            self.handshaker = Handshaker(self.mediawiki_index_url, self.consumer_token, callback=self.callback_url,
                                         user_agent=self.user_agent)

            # Step 1: Initialize -- ask MediaWiki for a temp key/secret for user
            # redirect -> authorization -> callback url
            self.redirect, self.request_token = self.handshaker.initiate(callback=self.callback_url)

        else:
            params_login = {
                'action': 'query',
                'meta': 'tokens',
                'type': 'login',
                'format': 'json'
            }

            # get login token
            login_token = self.s.post(self.mediawiki_api_url, data=params_login).json()['query']['tokens']['logintoken']

            if use_clientlogin:
                params = {
                    'action': 'clientlogin',
                    'username': user,
                    'password': pwd,
                    'logintoken': login_token,
                    'loginreturnurl': 'http://example.org/',
                    'format': 'json'
                }

                login_result = self.s.post(self.mediawiki_api_url, data=params).json()

                if debug:
                    print(login_result)

                if 'clientlogin' in login_result:
                    if login_result['clientlogin']['status'] != 'PASS':
                        clientlogin = login_result['clientlogin']
                        raise LoginError('Login failed ({}). Message: \'{}\''.format(clientlogin['messagecode'], clientlogin['message']))
                    elif debug:
                        print('Successfully logged in as', login_result['clientlogin']['username'])
                else:
                    error = login_result['error']
                    raise LoginError('Login failed ({}). Message: \'{}\''.format(error['code'], error['info']))

            else:
                params = {
                    'action': 'login',
                    'lgname': user,
                    'lgpassword': pwd,
                    'lgtoken': login_token,
                    'format': 'json'
                }

                login_result = self.s.post(self.mediawiki_api_url, data=params).json()

                if debug:
                    print(login_result)

                if login_result['login']['result'] != 'Success':
                    raise LoginError('Login failed. Reason: \'{}\''.format(login_result['login']['result']))
                elif debug:
                    print('Successfully logged in as', login_result['login']['lgusername'])

                if 'warnings' in login_result:
                    print('MediaWiki login warnings messages:')
                    for message in login_result['warnings']:
                        print('* {}: {}'.format(message, login_result['warnings'][message]['*']))

            self.generate_edit_credentials()

    def generate_edit_credentials(self):
        """
        request an edit token and update the cookie_jar in order to add the session cookie
        :return: Returns a json with all relevant cookies, aka cookie jar
        """
        params = {
            'action': 'query',
            'meta': 'tokens',
            'format': 'json'
        }
        response = self.s.get(self.mediawiki_api_url, params=params)
        self.edit_token = response.json()['query']['tokens']['csrftoken']

        return self.s.cookies

    def get_edit_cookie(self):
        """
        Can be called in order to retrieve the cookies from an instance of wbi_login.Login
        :return: Returns a json with all relevant cookies, aka cookie jar
        """
        if (time.time() - self.instantiation_time) > self.token_renew_period:
            self.generate_edit_credentials()
            self.instantiation_time = time.time()

        return self.s.cookies

    def get_edit_token(self):
        """
        Can be called in order to retrieve the edit token from an instance of wbi_login.Login
        :return: returns the edit token
        """
        if not self.edit_token or (time.time() - self.instantiation_time) > self.token_renew_period:
            self.generate_edit_credentials()
            self.instantiation_time = time.time()

        return self.edit_token

    def get_session(self):
        """
        returns the requests session object used for the login.
        :return: Object of type requests.Session()
        """
        return self.s

    def continue_oauth(self, oauth_callback_data=None):
        """
        Continuation of OAuth procedure. Method must be explicitly called in order to complete OAuth. This allows
        external entities, e.g. websites, to provide tokens through callback URLs directly.
        :param oauth_callback_data: The callback URL received to a Web app
        :type oauth_callback_data: bytes
        :return:
        """
        self.response_qs = oauth_callback_data

        if not self.response_qs:
            webbrowser.open(self.redirect)
            self.response_qs = input("Callback URL: ")

        # input the url from redirect after authorization
        response_qs = self.response_qs.split(b'?')[-1]

        # Step 3: Complete -- obtain authorized key/secret for "resource owner"
        access_token = self.handshaker.complete(self.request_token, response_qs)

        # input the access token to return a csrf (edit) token
        auth1 = OAuth1(self.consumer_token.key,
                       client_secret=self.consumer_token.secret,
                       resource_owner_key=access_token.key,
                       resource_owner_secret=access_token.secret)

        self.s.auth = auth1
        self.generate_edit_credentials()


class LoginError(Exception):
    """Raised when there is an issue with the login"""
    pass
