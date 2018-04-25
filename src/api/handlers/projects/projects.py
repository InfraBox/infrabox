import os
import requests
import uuid

from flask import g, abort, request
from flask_restplus import Resource, fields

from pyinfraboxutils import get_logger
from pyinfraboxutils.ibrestplus import api
from pyinfraboxutils.ibflask import auth_required, OK

from api.namespaces import project as ns

logger = get_logger('project')

project_model = api.model('Project', {
    'id': fields.String(required=True),
    'name': fields.String(required=True),
    'type': fields.String(required=True)
})

add_project_schema = {
    'type': "object",
    'properties': {
        'name': {'type': "string", 'pattern': "^[0-9a-zA-Z_-]+$", "minLength": 3, "maxLength": 20},
        'private': {'type': "boolean"},
        'type': {'type': "string", 'enum': ["upload", "gerrit", "github", "gitlab"]},
        'repo_name': {'type': "string"},
        'remote_id': {'type': 'integer'}
    },
    'required': ["name", "private", "type"]
}

add_project_model = ns.schema_model('AddProject', add_project_schema)


def get_headers(token_name, api_token):
    if token_name == 'github_api_token':
        headers = {
            "Authorization": "token " + api_token,
            "User-Agent": "InfraBox"
        }
    elif token_name == 'gitlab_api_token':
        headers = {
            'Authorization': 'Bearer %s' % api_token
        }
    else:
        headers = {}

    return headers


def prepare_project_info(project_type, data):
    project_info = {}

    if project_type == 'github':
        github_repo_name = data.get('repo_name', None)

        if not github_repo_name:
            abort(400, 'github repo_name not set')

        split = github_repo_name.split('/')
        owner = split[0]
        repo_name = split[1]

        project_info['owner'] = owner
        project_info['repo_name'] = repo_name
        project_info['token_name'] = 'github_api_token'
        project_info['repo_id_type'] = 'github_id'
        project_info['repo_owner_type'] = 'github_owner'
        project_info['api_url'] = '%s/repos/%s/%s' % (
            os.environ['INFRABOX_GITHUB_API_URL'],
            project_info['owner'], project_info['repo_name'])
        project_info['hook_url'] = '%s/repos/%s/%s/hooks' % (os.environ['INFRABOX_GITHUB_API_URL'],
                                                             owner, repo_name)
        project_info['hook_id_type'] = 'gitlab_hook_id'

    elif project_type == 'gitlab':
        gitlab_repo_name = data.get('repo_name', None)

        if not gitlab_repo_name:
            abort(400, 'gitlab repo_name not set')

        split = gitlab_repo_name.split('/')
        owner = split[0]
        repo_name = split[1]

        project_info['owner'] = owner
        project_info['repo_name'] = repo_name
        project_info['token_name'] = 'gitlab_api_token'
        project_info['repo_id_type'] = 'gitlab_id'
        project_info['repo_owner_type'] = 'gitlab_owner'
        project_info['repo_url'] = '%s/projects/%s' % (os.environ['INFRABOX_GITLAB_API_URL'],
                                                       data['remote_id'])
        project_info['hook_url'] = project_info['repo_url'] + '/hooks'
        project_info['hook_id_type'] = 'gitlab_hook_id'
    else:
        abort(400, 'Unknown project type')

    return project_info


def has_admin_perission(repo, typ):
    if typ == 'github':
        return repo['permissions']['admin']
    if typ == 'gitlab':
        if 'permissions' in repo:
            return repo['permissions']['project_access']['access_level'] >= 30

    return False


def unify_repo_data(repo_data, typ):
    if typ == 'github':
        return repo_data
    if typ == 'gitlab':
        data = {}
        data['id'] = repo_data['id']
        data['name'] = repo_data['name']
        data['html_url'] = repo_data['web_url']
        data['clone_url'] = repo_data['http_url_to_repo']
        data['private'] = (repo_data['visibility'] == 'private')
        data['owner'] = {'login': repo_data['owner']['name']}
        data['permissions'] = repo_data['permissions']
        return data


def prepare_webhook_config(typ):
    webhook_config = {}
    if typ == 'github':
        webhook_config = {
            'name': "web",
            'active': True,
            'events': [
                "create", "delete", "public", "pull_request", "push"
            ],
            'config': {
                'url': os.environ['INFRABOX_ROOT_URL'] + '/github/hook',
                'content_type': "json",
                'secret': os.environ['INFRABOX_GITHUB_WEBHOOK_SECRET']
            }
        }
    elif typ == 'gitlab':
        webhook_config = {
            'id': os.environ['INFRABOX_GITLAB_WEBHOOK_ID'],
            'url': os.environ['INFRABOX_ROOT_URL'] + '/gitlab/hook',
            'push_events': True,
            'token': os.environ['INFRABOX_GITLAB_WEBHOOK_SECRET']
        }
    else:
        abort(400, 'Unknown project type')

    return webhook_config


@ns.route('/')
class Projects(Resource):
    @auth_required(['user'], check_project_access=False)
    @api.marshal_list_with(project_model)
    def get(self):
        projects = g.db.execute_many_dict('''
            SELECT p.id, p.name, p.type FROM project p
            INNER JOIN collaborator co
            ON co.project_id = p.id
            AND %s = co.user_id
            ORDER BY p.name
        ''', [g.token['user']['id']])

        return projects

    @auth_required(['user'], check_project_access=False)
    @api.expect(add_project_model)
    def post(self):
        user_id = g.token['user']['id']

        b = request.get_json()
        name = b['name']
        typ = b['type']
        private = b['private']

        projects = g.db.execute_one_dict('''
            SELECT COUNT(*) as cnt
            FROM project p
            INNER JOIN collaborator co
            ON p.id = co.project_id
            AND co.user_id = %s
        ''', [user_id])

        if projects['cnt'] > 50:
            abort(400, 'too many projects')

        project = g.db.execute_one_dict('''
            SELECT *
            FROM project
            WHERE name = %s
        ''', [name])

        if project:
            abort(400, 'A project with this name already exists')

        if typ == 'gerrit':
            project = g.db.execute_one_dict('''
                                INSERT INTO project (name, type, public)
                                VALUES (%s, %s, %s) RETURNING id
                            ''', [name, typ, not private])
            project_id = project['id']

            g.db.execute('''
                                INSERT INTO collaborator (user_id, project_id, owner)
                                VALUES (%s, %s, true)
                            ''', [user_id, project_id])

            g.db.execute('''
                INSERT INTO repository (name, private, project_id, html_url, clone_url, github_id, gitlab_id)
                VALUES (%s, false, %s, '', '', 0, 0)
            ''', [name, project_id])

            g.db.commit()

            return OK('Project added')

        project_info = prepare_project_info(typ, b)

        user = g.db.execute_one_dict('''
                        SELECT %s FROM "user"
                        WHERE id = '%s'
                    ''' % (project_info['token_name'], user_id))

        if not user:
            abort(404)

        api_token = user[project_info['token_name']]
        headers = get_headers(project_info['token_name'], api_token)
        url = project_info['repo_url']

        r = requests.get(url, headers=headers, verify=False)

        if r.status_code != 200:
            abort(400, 'Failed to get repo')

        repo = unify_repo_data(r.json(), typ)

        if not has_admin_perission(repo, typ):
            abort(400, 'You are not allowed to connect this repo')

        r = g.db.execute_one_dict('''
                        SELECT *
                        FROM repository
                        WHERE %s = '%s'
                    ''' % (project_info['repo_id_type'], repo['id']))

        if r:
            abort('Repo already connected')

        project = g.db.execute_one_dict('''
                    INSERT INTO project (name, type, public)
                    VALUES (%s, %s, %s) RETURNING id
                ''', [name, typ, not private])
        project_id = project['id']

        g.db.execute('''
                    INSERT INTO collaborator (user_id, project_id, owner)
                    VALUES (%s, %s, true)
                ''', [user_id, project_id])

        g.db.execute('''
                        INSERT INTO repository (name, html_url, clone_url, %s,
                                                private, project_id, %s)
                        VALUES ('%s', '%s', '%s', '%s', '%s', '%s', '%s')
                    ''' % (project_info['repo_id_type'], project_info['repo_owner_type'], repo['name'], repo['html_url'], repo['clone_url'],
                          repo['id'], repo['private'], project_id, repo['owner']['login']))

        webhook_config = prepare_webhook_config(typ)
        hook_url = project_info['hook_url']

        r = requests.post(hook_url, headers=headers, json=webhook_config, verify=False)

        if r.status_code != 201:
            abort(400, 'Failed to create webhook')

        hook = r.json()

        g.db.execute('''
                        UPDATE repository SET %s = '%s'
                        WHERE %s = '%s'
                    ''' % (project_info['hook_id_type'], hook['id'],
                          project_info['repo_id_type'], repo['id']))

        g.db.commit()

        return OK('Project added')


@ns.route('/name/<project_name>')
class ProjectName(Resource):
    @auth_required(['user'], check_project_access=False, allow_if_public=True)
    @api.marshal_list_with(project_model)
    def get(self, project_name):
        project = g.db.execute_one_dict('''
            SELECT id, name, type
            FROM project
            WHERE name = %s
        ''', [project_name])

        if not project:
            abort(404)

        return project


@ns.route('/<project_id>/')
class Project(Resource):
    @auth_required(['user'], allow_if_public=True)
    @api.marshal_list_with(project_model)
    def get(self, project_id):
        projects = g.db.execute_many_dict('''
            SELECT p.id, p.name, p.type
            FROM project p
            WHERE id = %s
        ''', [project_id])

        return projects

    @auth_required(['user'], check_project_owner=True)
    def delete(self, project_id):

        project = g.db.execute_one_dict('''
            DELETE FROM project WHERE id = %s RETURNING type
        ''', [project_id])

        if not project:
            abort(404)

        if project['type'] == 'github':
            repo = g.db.execute_one_dict('''
                SELECT name, github_owner, github_hook_id
                FROM repository
                WHERE project_id = %s
            ''', [project_id])

            gh_owner = repo['github_owner']
            gh_hook_id = repo['github_hook_id']
            gh_repo_name = repo['name']

            user = g.db.execute_one_dict('''
                SELECT github_api_token
                FROM "user"
                WHERE id = %s
            ''', [g.token['user']['id']])
            gh_api_token = user['github_api_token']

            headers = {
                "Authorization": "token " + gh_api_token,
                "User-Agent": "InfraBox"
            }
            url = '%s/repos/%s/%s/hooks/%s' % (os.environ['INFRABOX_GITHUB_API_URL'],
                                               gh_owner, gh_repo_name, gh_hook_id)

            # TODO(ib-steffen): allow custom ca bundles
            requests.delete(url, headers=headers, verify=False)

        if project['type'] == 'gitlab':
            repo = g.db.execute_one_dict('''
                SELECT gitlab_hook_id, gitlab_id
                FROM repository
                WHERE project_id = %s
            ''', [project_id])

            gl_id = repo['gitlab_id']
            gl_hook_id = repo['gitlab_hook_id']

            user = g.db.execute_one_dict('''
                SELECT gitlab_api_token
                FROM "user"
                WHERE id = %s
            ''', [g.token['user']['id']])
            gl_api_token = user['gitlab_api_token']

            headers = get_headers('gitlab_api_token', gl_api_token)
            url = '%s/projects/%s/hooks/%s' % (os.environ['INFRABOX_GITLAB_API_URL'], gl_id, gl_hook_id)
            requests.delete(url, headers=headers, verify=False)


        # TODO: delete all tables
        g.db.execute('''
            DELETE FROM repository
            WHERE project_id = %s
        ''', [project_id])

        g.db.execute('''
            DELETE FROM collaborator
            WHERE project_id = %s
        ''', [project_id])

        g.db.commit()

        return OK('deleted project')
