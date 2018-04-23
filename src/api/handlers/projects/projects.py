import os
import requests

from flask import g, abort, request
from flask_restplus import Resource, fields

from pyinfraboxutils import get_logger
from pyinfraboxutils.ibrestplus import api
from pyinfraboxutils.ibflask import auth_required, OK

from api.namespaces import project as ns


logger = get_logger('project')

repo_model = api.model('ProjectRepo', {
    'name': fields.String(required=True),
    'link': fields.String(required=True),
})

project_model = api.model('Project', {
    'id': fields.String(required=True),
    'name': fields.String(required=True),
    'type': fields.String(required=True),
    'repo': fields.Nested(repo_model)
})

add_project_schema = {
    'type': "object",
    'properties': {
        'name': {'type': "string", 'pattern': r"^[0-9a-zA-Z_\-/]+$", "minLength": 3},
        'private': {'type': "boolean"},
        'type': {'type': "string", 'enum': ["upload", "gerrit", "github"]},
        'github_repo_name': {'type': "string"}
    },
    'required': ["name", "private", "type"]
}

add_project_model = ns.schema_model('AddProject', add_project_schema)

def convert_to_result_project(p):
    repo = None
    if p['type'] == 'github':
        repo = {
            'name': p['github_owner'] + '/' + p['repo_name'],
            'link': p['html_url']
        }
    elif p['type'] == 'gerrit':
        repo = {
            'name': p['repo_name'],
            'link': p['html_url']
        }

    return {
        'name': p['name'],
        'id': p['id'],
        'type': p['type'],
        'repo': repo
    }


@ns.route('/')
class Projects(Resource):

    @auth_required(['user'], check_project_access=False)
    @api.marshal_list_with(project_model)
    def get(self):
        projects = g.db.execute_many_dict('''
            SELECT p.id, p.name, p.type, r.html_url, r.name repo_name, r.github_owner
            FROM project p
            INNER JOIN collaborator co
            ON co.project_id = p.id
            AND %s = co.user_id
            LEFT OUTER JOIN repository r
            ON r.project_id = p.id
            ORDER BY p.name
        ''', [g.token['user']['id']])

        r = []
        for p in projects:
            r.append(convert_to_result_project(p))

        return r

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


        if typ == 'github':
            github_repo_name = b.get('github_repo_name', None)

            if not github_repo_name:
                abort(400, 'github_repo_name not set')

            split = github_repo_name.split('/')
            owner = split[0]
            repo_name = split[1]

            user = g.db.execute_one_dict('''
                SELECT github_api_token
                FROM "user"
                WHERE id = %s
            ''', [user_id])

            if not user:
                abort(404)

            api_token = user['github_api_token']

            headers = {
                "Authorization": "token " + api_token,
                "User-Agent": "InfraBox"
            }
            url = '%s/repos/%s/%s' % (os.environ['INFRABOX_GITHUB_API_URL'],
                                      owner, repo_name)

            # TODO(ib-steffen): allow custom ca bundles
            r = requests.get(url, headers=headers, verify=False)

            if r.status_code != 200:
                abort(400, 'Failed to get github repo')

            repo = r.json()

            if not repo['permissions']['admin']:
                abort(400, 'You are not allowed to connect this repo')

            r = g.db.execute_one_dict('''
                SELECT *
                FROM repository
                WHERE github_id = %s
            ''', [repo['id']])

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


        if typ == 'github':
            split = github_repo_name.split('/')
            owner = split[0]
            repo_name = split[1]

            g.db.execute('''
                INSERT INTO repository (name, html_url, clone_url, github_id,
                                        private, project_id, github_owner)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            ''', [repo['name'], repo['html_url'], repo['clone_url'],
                  repo['id'], repo['private'], project_id, repo['owner']['login']])

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

            headers = {
                "Authorization": "token " + api_token,
                "User-Agent": "InfraBox"
            }
            url = '%s/repos/%s/%s/hooks' % (os.environ['INFRABOX_GITHUB_API_URL'],
                                            owner, repo_name)

            # TODO(ib-steffen): allow custom ca bundles
            r = requests.post(url, headers=headers, json=webhook_config, verify=False)

            if r.status_code != 201:
                abort(400, 'Failed to create github webhook')

            hook = r.json()

            g.db.execute('''
                UPDATE repository SET github_hook_id = %s
                WHERE github_id = %s
            ''', [hook['id'], repo['id']])
        elif typ == 'gerrit':
            g.db.execute('''
                INSERT INTO repository (name, private, project_id, html_url, clone_url, github_id)
                VALUES (%s, false, %s, '', '', 0)
            ''', [name, project_id])

        g.db.commit()

        return OK('Project added')

@ns.route('/name/<project_name>')
class ProjectName(Resource):

    @auth_required(['user'], check_project_access=False, allow_if_public=True)
    @api.marshal_with(project_model)
    def get(self, project_name):
        project = g.db.execute_one_dict('''
            SELECT p.id, p.name, p.type, r.html_url, r.name repo_name, r.github_owner
            FROM project p
            LEFT OUTER JOIN repository r
            ON r.project_id = p.id
            WHERE p.name = %s
        ''', [project_name])

        if not project:
            abort(404)

        return convert_to_result_project(project)


@ns.route('/<project_id>/')
class Project(Resource):

    @auth_required(['user'], allow_if_public=True)
    @api.marshal_with(project_model)
    def get(self, project_id):
        project = g.db.execute_one_dict('''
            SELECT p.id, p.name, p.type, r.html_url, r.name repo_name, r.github_owner
            FROM project p
            LEFT OUTER JOIN repository r
            ON r.project_id = p.id
            WHERE p.id = %s
        ''', [project_id])

        if not project:
            abort(404)

        return convert_to_result_project(project)

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
