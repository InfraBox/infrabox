import json
import hashlib
import hmac

import requests

from pyinfraboxutils import get_env, get_logger
from pyinfraboxutils.ibbottle import InfraBoxPostgresPlugin
from pyinfraboxutils.db import connect_db

from bottle import post, run, request, response, install, get

logger = get_logger("gitlab")


def remove_ref(ref):
    return "/".join(ref.split("/")[2:])


def res(status, message):
    response.status = status
    return {"message": message}


def get_commits(url, token):
    headers = {
        "Private-Token:" + token
    }

    r = requests.get(url, headers=headers, verify=False)
    result = []
    result.extend(r.json())

    return result


class Trigger(object):
    def __init__(self, conn):
        self.conn = conn

    def execute(self, stmt, args=None, fetch=True):
        cur = self.conn.cursor()
        cur.execute(stmt, args)

        if not fetch:
            cur.close()
            return None

        result = cur.fetchall()
        cur.close()
        return result

    def get_owner_token(self, repo_id):
        return self.execute('''
            SELECT gitlab_api_token FROM "user" u
            INNER JOIN collaborator co
                ON co.user_id = u.id
                AND co.owner = true
            INNER JOIN project p
                ON co.project_id = p.id
            INNER JOIN repository r
                ON r.gitlab_id = %s
                AND r.project_id = p.id
        ''', [repo_id])[0][0]

    def create_build(self, commit_id, project_id):
        build_no = self.execute('''
                    SELECT max(build_number) + 1 AS build_no
                    FROM build AS b
                    WHERE b.project_id = %s
                ''', [project_id])[0][0]

        if not build_no:
            build_no = 1

        result = self.execute('''
            INSERT INTO build (commit_id, build_number, project_id)
            VALUES (%s, %s, %s)
            RETURNING id
        ''', [commit_id, build_no, project_id])
        build_id = result[0][0]
        return build_id

    def create_job(self, commit_id, clone_url, build_id, project_id, gitlab_private_repo, branch, env=None, fork=False):
        git_repo = {
            "commit": commit_id,
            "clone_url": clone_url,
            "gitlab_private_repo": gitlab_private_repo,
            "branch": branch,
            "fork": fork
        }

        self.execute('''
            INSERT INTO job (id, state, build_id, type,
                             name, project_id, build_only,
                             dockerfile, cpu, memory, repo, env_var, cluster_name))
            VALUES (gen_random_uuid(), 'queued', %s, 'create_job_matrix',
                    'Create Jobs', %s, false, '', 1, 1024, %s, %s, 'master)
        ''', [build_id, project_id, json.dumps(git_repo), env], fetch=False)

    def create_push(self, c, repository, branch, tag):
        #if not c['distinct']:
        #    return

        result = self.execute('''
            SELECT id, project_id, private
            FROM repository
            WHERE gitlab_id = %s''', [repository['id']])[0]

        repo_id = result[0]
        project_id = result[1]
        gitlab_repo_private = result[2]
        commit_id = None

        result = self.execute('''
            SELECT id
            FROM "commit"
            WHERE id = %s
                AND project_id = %s
        ''', [c['id'], project_id])

        commit_id = c['id']

        if not result:
            status_url = repository['web_url'].format(sha=c['id'])
            result = self.execute('''
                INSERT INTO "commit" (
                    id, message, repository_id, timestamp,
                    author_name, author_email, author_username,
                    committer_name, committer_email, committer_username, url, branch, project_id,
                    tag, github_status_url)
                VALUES (%s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s)
                RETURNING id
            ''', [c['id'],
                  c['message'],
                  repo_id,
                  c['timestamp'],
                  c['author']['name'],
                  c['author']['email'],
                  c['author'].get('username', None),
                  c['committer']['name'],
                  c['committer']['email'],
                  c['committer'].get('username', None),
                  c['url'],
                  branch,
                  project_id,
                  tag,
                  status_url])

        if tag:
            self.execute('''
                UPDATE "commit" SET tag = %s WHERE id = %s AND project_id = %s
            ''', [tag, c['id'], project_id], fetch=False)

        build_id = self.create_build(commit_id, project_id)
        self.create_job(c['id'], repository['git_http_url'], build_id,
                        project_id, gitlab_repo_private, branch)

    def handle_push(self, event):
        result = self.execute('''
            SELECT project_id FROM repository WHERE gitlab_id = %s;
        ''', [event['project_id']])
        project_id = result[0]

        result = self.execute('''
            SELECT build_on_push FROM project WHERE id = %s;
        ''', [project_id])[0]

        if not result[0]:
            return res(200, 'build_on_push not set')

        branch = None
        tag = None
        commit = None

        ref = event['ref']
        if ref.startswith('refs/tags'):
            tag = remove_ref(ref)
            if event['total_commits_count'] > 0:
                commit = event['commits'][0]
        else:
            branch = remove_ref(ref)
            if event['commits']:
                commit = event['commits'][-1]

        token = self.get_owner_token(event['project_id'])

        if not token:
            return res(200, 'no token')

        if commit:
            commit['committer'] = {'name': event.get('user_name', None),
                                   'email': event.get('user_email', None),
                                   'username': event.get('user_username', None)}
            self.create_push(commit, event['project'], branch, tag)

        self.conn.commit()
        return res(200, 'ok')

    def handle_pull_request(self, event):
        if event['action'] not in ['opened', 'reopened', 'synchronize']:
            return

        result = self.execute('''
            SELECT id, project_id, private FROM repository WHERE gitlab_id = %s;
        ''', [event['project_id']])

        if not result:
            return res(404, "Unknown repository")

        result = result[0]

        repo_id = result[0]
        project_id = result[1]
        gitlab_repo_private = result[2]

        result = self.execute('''
            SELECT build_on_push FROM project WHERE id = %s;
        ''', [project_id])[0]

        if not result[0]:
            return res(200, 'build_on_push not set')

        token = self.get_owner_token(event['project_id'])

        if not token:
            return res(200, 'no token')

        commits = get_commits(event['pull_request']['commits_url'], token)

        hc = None
        for commit in commits:
            if commit['sha'] == event['pull_request']['head']['sha']:
                hc = commit
                break

        if not hc:
            logger.error('Head commit not found: %s', event['pull_request']['head']['sha'])
            logger.error(json.dumps(commits, indent=4))
            return res(500, 'Internal Server Error')

        is_fork = event['pull_request']['head']['repo']['fork']

        result = self.execute('''
            SELECT id FROM pull_request WHERE project_id = %s and github_pull_request_id = %s
        ''', [repo_id, event['pull_request']['id']])

        if not result:
            result = self.execute('''
                INSERT INTO pull_request (project_id, github_pull_request_id,
                                         title, url)
                VALUES (%s, %s, %s, %s) RETURNING ID
            ''', [project_id,
                  event['pull_request']['id'],
                  event['pull_request']['title'],
                  event['pull_request']['html_url']
                  ])
            pr_id = result[0][0]

        result = self.execute('''
            SELECT id
            FROM "commit"
            WHERE id = %s
                AND project_id = %s
        ''', [hc['sha'], project_id])

        committer_login = None
        if hc.get('committer', None):
            committer_login = hc['committer']['login']

        branch = event['pull_request']['head']['ref']

        env = json.dumps({
            "GITLAB_PULL_REQUEST_BASE_LABEL": event['pull_request']['base']['label'],
            "GITLAB_PULL_REQUEST_BASE_REF": event['pull_request']['base']['ref'],
            "GITLAB_PULL_REQUEST_BASE_SHA": event['pull_request']['base']['sha'],
            "GITLAB_PULL_REQUEST_BASE_REPO_CLONE_URL": event['pull_request']['base']['repo']['clone_url']
        })

        if not result:
            result = self.execute('''
                INSERT INTO "commit" (
                    id, message, repository_id, timestamp,
                    author_name, author_email, author_username,
                    committer_name, committer_email, committer_username, url, project_id,
                    branch, pull_request_id, github_status_url)
                VALUES (%s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s)
                RETURNING id
            ''', [hc['sha'], hc['commit']['message'],
                  repo_id, hc['commit']['author']['date'], hc['commit']['author']['name'],
                  hc['commit']['author']['email'], hc['author']['login'],
                  hc['commit']['committer']['name'],
                  hc['commit']['committer']['email'],
                  committer_login, hc['html_url'], project_id, branch, pr_id,
                  event['pull_request']['statuses_url']])
            commit_id = result[0][0]

            build_id = self.create_build(commit_id, project_id)
            self.create_job(event['pull_request']['head']['sha'],
                            event['pull_request']['head']['repo']['clone_url'],
                            build_id, project_id, gitlab_repo_private, branch, env=env, fork=is_fork)

            self.conn.commit()

        return res(200, 'ok')


def sign_blob(key, blob):
    return 'sha1=' + hmac.new(key, blob, hashlib.sha1).hexdigest()


@post('/gitlab/hook')
def trigger_build(conn):
    headers = dict(request.headers)

    if 'X-Gitlab-Event' not in headers:
        return res(400, "X-Gitlab-Event not set")

    if 'X-Gitlab-Token' not in headers:
        return res(400, "X-Gitlab-Token not set")

    event = headers['X-Gitlab-Event']
    hook_token = headers['X-Gitlab-Token']
    #pylint: disable=no-member
    body = request.body.read()
    secret = get_env('INFRABOX_GITLAB_WEBHOOK_SECRET')
    #signed = sign_blob(secret, body)

    if secret != hook_token:
        return res(400, "X-Gitlab-Token does not match blob signature")

    trigger = Trigger(conn)
    if event == 'Push Hook':
        return trigger.handle_push(request.json)
    #elif event == 'pull_request':
    #    return trigger.handle_pull_request(request.json)

    return res(200, "OK")

@get('/ping')
def ping():
    return res(200, "OK")


def main():
    get_env('INFRABOX_SERVICE')
    get_env('INFRABOX_VERSION')
    get_env('INFRABOX_DATABASE_DB')
    get_env('INFRABOX_DATABASE_USER')
    get_env('INFRABOX_DATABASE_PASSWORD')
    get_env('INFRABOX_DATABASE_HOST')
    get_env('INFRABOX_DATABASE_PORT')
    get_env('INFRABOX_GITLAB_WEBHOOK_SECRET')

    connect_db()  # Wait until DB is ready

    install(InfraBoxPostgresPlugin())
    run(host='0.0.0.0', port=8080)


if __name__ == '__main__':
    main()
