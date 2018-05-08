import uuid
import os
import requests

from flask import g, request, abort, redirect

from flask_restplus import Resource

from pyinfraboxutils import get_logger
from pyinfraboxutils.token import encode_user_token
from pyinfraboxutils.ibflask import auth_required

from api.namespaces import gitlab, gitlab_auth

logger = get_logger('gitlab')

GITLAB_USER_PROFILE_URL = os.environ['INFRABOX_GITLAB_API_URL'] + '/users'

GITLAB_APPLICATION_ID = os.environ['INFRABOX_GITLAB_APPLICATION_ID']
GITLAB_APPLICATION_SECRET = os.environ['INFRABOX_GITLAB_APPLICATION_SECRET']
GITLAB_CALLBACK_URL = os.environ['INFRABOX_ROOT_URL'] + "/gitlab/auth/callback"

GITLAB_AUTHORIZATION_URL = os.environ['INFRABOX_GITLAB_OAUTH_URL'] + '/authorize'
GITLAB_TOKEN_URL = os.environ['INFRABOX_GITLAB_OAUTH_URL'] + '/token'

states = {}


def get_next_page(r):
    pass


def get_gitlab_api(url, token):
    headers = {
        'Authorization': 'Bearer %s' % token
    }
    url = os.environ['INFRABOX_GITLAB_API_URL'] + url

    #TODO(andrew) somehow request to gitlab api
    #takes too long to proceed
    r = requests.get(url, headers=headers, verify=False)

    return r.json()


@gitlab_auth.route('/auth/connect')
class Connect(Resource):
    @auth_required(['user'], check_project_access=False)
    def get(self):
        if os.environ['INFRABOX_GITLAB_LOGIN_ENABLED'] == 'true':
            abort(404)

        user_id = g.token['user']['id']
        uid = str(uuid.uuid4())

        g.db.execute('''
                UPDATE "user" SET gitlab_id = null, gitlab_api_token = %s
                WHERE id = %s
            ''', [uid, user_id])

        g.db.commit()

        state = str(uuid.uuid4())
        url = GITLAB_AUTHORIZATION_URL
        url += '?client_id=%s&redirect_uri=%s&response_type=code&state=%s' % (
            GITLAB_APPLICATION_ID, GITLAB_CALLBACK_URL, state)

        states[str(state)] = True
        return redirect(url)


@gitlab.route('/projects')
class Projects(Resource):
    @auth_required(['user'], check_project_access=False)
    def get(self):
        logger.error('\n\nPRJ11\n\n')
        user_id = g.token['user']['id']
        user = g.db.execute_one_dict('''
                SELECT gitlab_api_token, gitlab_id
                FROM "user"
                WHERE id = %s
            ''', [user_id])

        if not user:
            abort(404)

        token = user['gitlab_api_token']

        gitlab_repos = get_gitlab_api('/users/%i/projects' % user['gitlab_id'], token)

        for r in gitlab_repos:
            if r['visibility'] == 'private':
                r['private'] = True

        repos = g.db.execute_many_dict('''
                    select gitlab_id from collaborator co
                    INNER JOIN repository r
                    ON co.project_id = r.project_id
                    WHERE user_id = %s
                    AND gitlab_id is not null
                ''', [user_id])

        for gr in gitlab_repos:
            gr['connected'] = False

            for r in repos:
                if r['gitlab_id'] == gr['id']:
                    gr['connected'] = True
                    break

        return gitlab_repos


@gitlab_auth.route('/auth')
class Auth(Resource):
    def get(self):
        if os.environ['INFRABOX_GITLAB_LOGIN_ENABLED'] != 'true':
            abort(404)

        state = uuid.uuid4()
        url = GITLAB_AUTHORIZATION_URL
        url += '?client_id=%s&redirect_uri=%s&response_type=code&state=%s' % (
            GITLAB_APPLICATION_ID, GITLAB_CALLBACK_URL, state)

        states[str(state)] = True

        return redirect(url)


@gitlab_auth.route('/auth/callback')
class Login(Resource):
    def get(self):
        logger.error('\n\nAUTH!!\n\n')
        state = request.args.get('state')
        code = request.args.get('code')

        t = request.args.get('t', None)

        if not states.get(state, None):
            abort(401)

        del states[state]
        logger.error('\n\nAUTH22\n\n')
        r = requests.post(GITLAB_TOKEN_URL, json={
            'client_id': GITLAB_APPLICATION_ID,
            'client_secret': GITLAB_APPLICATION_SECRET,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': GITLAB_CALLBACK_URL
        }, verify=False)
        logger.error('\n\nAUTH33\n\n')
        if r.status_code != 200:
            logger.error(r.text)
            abort(500)

        result = r.json()

        access_token = result['access_token']

        # Temporary disable org check
        # check_org(access_token)

        r = requests.get(GITLAB_USER_PROFILE_URL, headers={
            'Authorization': 'Bearer %s' % access_token
        }, verify=False)

        gu = r.json()[0]

        gitlab_id = gu['id']

        if os.environ['INFRABOX_GITLAB_LOGIN_ENABLED'] == 'true':
            user = g.db.execute_one_dict('''
                SELECT id FROM "user"
                WHERE gitlab_id = %s
            ''', [gitlab_id])

            if not user:
                user = g.db.execute_one_dict('''
                    INSERT INTO "user" (gitlab_id, username, avatar_url, name)
                    VALUES (%s, %s, %s, %s) RETURNING id
                ''', [gitlab_id, gu['username'], gu['avatar_url'], gu['name']])

            user_id = user['id']
        else:
            if not t:
                abort(404)

            user = g.db.execute_one_dict('''
                SELECT id
                FROM "user"
                WHERE gitlab_api_token = %s
            ''', [t])

            if not user:
                abort(404)

            user_id = user['id']

        g.db.execute('''
            UPDATE "user" SET gitlab_api_token = %s, gitlab_id = %s
            WHERE id = %s
        ''', [access_token, gitlab_id, user_id])

        g.db.commit()

        token = encode_user_token(user_id)
        url = 'http://localhost:8080' + '/dashboard/' #os.environ['INFRABOX_ROOT_URL']
        res = redirect(url)
        res.set_cookie('token', token)
        return res
