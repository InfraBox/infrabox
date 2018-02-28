import unittest

from temp_tools import TestClient

class ApiTestTemplate(unittest.TestCase):

    def setUp(self):
        TestClient.execute('TRUNCATE "user"')
        TestClient.execute('TRUNCATE project')
        TestClient.execute('TRUNCATE collaborator')
        TestClient.execute('TRUNCATE repository')
        TestClient.execute('TRUNCATE commit')
        TestClient.execute('TRUNCATE build')
        TestClient.execute('TRUNCATE job')
        TestClient.execute('TRUNCATE source_upload')

        self.project_id = '1514af82-3c4f-4bb5-b1da-a89a0ced5e6f'
        self.user_id = '2514af82-3c4f-4bb5-b1da-a89a0ced5e6f'
        self.repo_id = '3514af82-3c4f-4bb5-b1da-a89a0ced5e6f'
        self.build_id = '4514af82-3c4f-4bb5-b1da-a89a0ced5e6f'
        self.job_id = '1454af82-4c4f-4bb5-b1da-a54a0ced5e6f'
        self.job_name = ''
        self.sha = 'd670460b4b4aece5915caf5c68d12f560a9fe3e4'
        self.author_name = 'author_name1'
        self.author_email = 'author@email.1'
        self.source_upload_id = '1423af82-3c4f-5bb5-b1da-a23a0ced5e6f'

        self.job_headers = TestClient.get_job_authorization(self.job_id)

        TestClient.execute("""
                INSERT INTO collaborator (user_id, project_id, owner)
                VALUES (%s, %s, true);
            """, (self.user_id, self.project_id))

        TestClient.execute("""
                INSERT INTO "user" (id, github_id, username,
                    avatar_url)
                VALUES (%s, 1, 'testuser', 'url');
            """, (self.user_id,))

        TestClient.execute("""
                INSERT INTO project(id, name, type)
                VALUES (%s, 'testproject', 'upload');
            """, (self.project_id,))

        TestClient.execute("""
                INSERT INTO repository(id, name, html_url, clone_url, github_id, project_id, private)
                VALUES (%s, 'testrepo', 'url', 'clone_url', 0, %s, true);
            """, (self.repo_id, self.project_id))

        TestClient.execute("""
                       INSERT INTO job (id, state, build_id, type, name, project_id,
                                 build_only, dockerfile, cpu, memory)
                VALUES (%s, 'queued', %s, 'run_docker_compose',
                        %s, %s, false, '', 1, 512)
                   """, (self.job_id, self.build_id, self.job_name, self.project_id))

        # TestClient.execute("""
        #                   INSERT INTO job (id, state, build_id, type, name, project_id,
        #                             build_only, dockerfile, cpu, memory)
        #            VALUES (%s, 'queued', %s, 'run_docker_compose',
        #                    'job_name1', %s, false, '', 1, 512)
        #               """, (self.job_id2, self.build_id, self.project_id))
        #
        TestClient.execute("""INSERT INTO build (id, project_id, build_number, commit_id, source_upload_id)
                                  VALUES (%s, %s, 1, %s, %s)""",
                           (self.build_id, self.project_id, self.sha, self.source_upload_id))

        TestClient.execute("""INSERT INTO commit (id, repository_id, "timestamp", project_id, author_name,
                                  author_email, committer_name, committer_email, url, branch)
                                          VALUES (%s, %s, now(), %s, %s, %s, %s, %s, 'url1', 'branch1')""",
                           (self.sha, self.repo_id, self.project_id,
                            self.author_name, self.author_email, self.author_name, self.author_email))

        #TestClient.execute("""
        #                       INSERT INTO source_upload (id, project_id, filename, filesize)
        #                       VALUES (%s, %s, %s, %s);
        #                   """, (self.source_upload_id, self.project_id, self.filename, self.filesize))
#