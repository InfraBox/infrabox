import json
import os
import shutil
from Configuration import Configuration
from Crypto.PublicKey import RSA
from Installer import Installer


class DockerCompose(Installer):
    def __init__(self, args):
        super(DockerCompose, self).__init__(args)
        self.config = Configuration()

    def setup_job_git(self):
        self.config.add('services.job-git.image',
                        '%s/job-git:%s' % (self.args.docker_registry, self.args.version))

        if self.args.gerrit_enabled:
            gerrit_key = os.path.join(self.args.o, 'gerrit_id_rsa')
            self.config.append('services.job-git.volumes', [
                '%s:/tmp/gerrit/id_rsa' % gerrit_key,
                ])
            self.config.append('services.job-git.environment', self.get_gerrit_env())

    def setup_api(self):
        self.config.append('services.api.environment', [
            'INFRABOX_ROOT_URL=%s' % self.args.root_url,
            'INFRABOX_GENERAL_REPORT_ISSUE_URL=%s' % self.args.general_report_issue_url
        ])

        self.config.add('services.api.image',
                        '%s/api:%s' % (self.args.docker_registry, self.args.version))

        self.config.append('services.api.volumes', [
            '%s:/var/run/secrets/infrabox.net/rsa/id_rsa' % os.path.join(self.args.o, 'id_rsa'),
            '%s:/var/run/secrets/infrabox.net/rsa/id_rsa.pub' % os.path.join(self.args.o, 'id_rsa.pub'),
            ])

        if self.args.gerrit_enabled:
            self.config.append('services.api.environment', self.get_gerrit_env())

    def setup_rsa(self):
        new_key = RSA.generate(bits=2048)
        public_key = new_key.publickey().exportKey()
        private_key = new_key.exportKey()

        with open(os.path.join(self.args.o, 'id_rsa'), 'w+') as out:
            out.write(private_key)

        with open(os.path.join(self.args.o, 'id_rsa.pub'), 'w+') as out:
            out.write(public_key)

    def setup_docker_registry(self):
        self.required_option('docker-registry')
        self.config.add('services.docker-registry-auth.image',
                        '%s/docker-registry-auth:%s' % (self.args.docker_registry, self.args.version))
        self.config.add('services.docker-registry-nginx.image',
                        '%s/docker-registry-nginx:%s' % (self.args.docker_registry, self.args.version))
        self.config.add('services.minio-init.image',
                        '%s/docker-compose-minio-init:%s' % (self.args.docker_registry, self.args.version))
        self.config.add('services.static.image',
                        '%s/static"%s' % (self.args.docker_registry, self.args.version))

        self.config.add('services.static.image',
                        '%s/static:%s' % (self.args.docker_registry, self.args.version))

        self.config.append('services.docker-registry-auth.volumes', [
            '%s:/var/run/secrets/infrabox.net/rsa/id_rsa.pub' % os.path.join(self.args.o, 'id_rsa.pub'),
            ])

    def setup_scheduler(self):
        self.config.add('services.scheduler.image',
                        '%s/scheduler-docker-compose:%s' % (self.args.docker_registry, self.args.version))

        daemon_config = os.path.join(self.args.o, 'daemon.json')

        json.dump({'insecure-registry': ['nginx-ingress'], 'disable-legacy-registry': True}, open(daemon_config, 'w'))

        self.config.append('services.scheduler.environment', [
            'INFRABOX_DOCKER_REGISTRY=%s' % self.args.docker_registry,
            'INFRABOX_JOB_VERSION=%s' % self.args.version
        ])

        self.config.append('services.scheduler.volumes', [
            '%s:/etc/docker/daemon.json' % daemon_config,
            '%s:/var/run/secrets/infrabox.net/rsa/id_rsa' % os.path.join(self.args.o, 'id_rsa'),
            '%s:/var/run/secrets/infrabox.net/rsa/id_rsa.pub' % os.path.join(self.args.o, 'id_rsa.pub'),
            ])

        if self.args.gerrit_enabled:
            self.config.append('services.scheduler.environment', self.get_gerrit_env())

    def setup_nginx_ingress(self):
        self.config.add('services.nginx-ingress.image',
                        '%s/docker-compose-ingress:%s' % (self.args.docker_registry, self.args.version))

    def get_gerrit_env(self):
        return [
            'INFRABOX_GERRIT_ENABLED=true',
            'INFRABOX_GERRIT_HOSTNAME=%s' % self.args.gerrit_hostname,
            'INFRABOX_GERRIT_USERNAME=%s' % self.args.gerrit_username,
            'INFRABOX_GERRIT_PORT=%s' % self.args.gerrit_port,
            'INFRABOX_GERRIT_KEY_FILENAME=/root/.ssh/id_rsa',
            ]

    def setup_gerrit(self):
        if not self.args.gerrit_enabled:
            return

        self.required_option('gerrit-hostname')
        self.required_option('gerrit-port')
        self.required_option('gerrit-username')
        self.required_option('gerrit-private-key')

        Installer.check_file_exists(self.args.gerrit_private_key)

        self.config.add('services.gerrit-trigger.image',
                        '%s/gerrit-trigger:%s' % (self.args.docker_registry, self.args.version))
        self.config.append('services.gerrit-trigger.networks', ['infrabox'])

        self.config.append('services.gerrit-trigger.environment', [
            'INFRABOX_SERVICE=gerrit-trigger',
            'INFRABOX_VERSION=%s' % self.args.version
        ])

        self.config.append('services.gerrit-trigger.environment', self.get_gerrit_env())

        gerrit_key = os.path.join(self.args.o, 'gerrit_id_rsa')
        shutil.copyfile(self.args.gerrit_private_key, gerrit_key)
        self.config.append('services.gerrit-trigger.volumes', [
            '%s:/tmp/gerrit/id_rsa' % gerrit_key,
            ])

    def setup_ldap(self):
        if self.args.ldap_enabled:
            self.required_option('ldap-dn')
            self.required_option('ldap-password')
            self.required_option('ldap-base')
            self.required_option('ldap-url')

            env = [
                "INFRABOX_ACCOUNT_LDAP_ENABLED=true",
                "INFRABOX_ACCOUNT_LDAP_BASE=%s" % self.args.ldap_base,
                "INFRABOX_ACCOUNT_LDAP_URL=%s" % self.args.ldap_url,
                "INFRABOX_ACCOUNT_LDAP_DN=%s" % self.args.ldap_dn,
                "INFRABOX_ACCOUNT_LDAP_PASSWORD=%s" % self.args.ldap_password,
                "INFRABOX_ACCOUNT_SIGNUP_ENABLED=false"
            ]

        else:
            env = [
                "INFRABOX_ACCOUNT_SIGNUP_ENABLED=true"
            ]

        self.config.append('services.api.environment', env)

    def setup_database(self):
        if self.args.database == 'postgres':
            self.required_option('postgres-host')
            self.required_option('postgres-port')
            self.required_option('postgres-username')
            self.required_option('postgres-password')
            self.required_option('postgres-database')

            env = [
                'INFRABOX_DATABASE_USER=%s' % self.args.postgres_username,
                'INFRABOX_DATABASE_PASSWORD=%s' % self.args.postgres_password,
                'INFRABOX_DATABASE_HOST=%s' % self.args.postgres_host,
                'INFRABOX_DATABASE_PORT=%s' % self.args.postgres_port,
                'INFRABOX_DATABASE_DB=%s' % self.args.postgres_database
            ]
        else:
            if self.args.database:
                super(DockerCompose, self).getLogger().warn("--database=%s not supported", self.args.database)

            env = [
                'INFRABOX_DATABASE_USER=postgres',
                'INFRABOX_DATABASE_PASSWORD=postgres',
                'INFRABOX_DATABASE_HOST=postgres',
                'INFRABOX_DATABASE_PORT=5432',
                'INFRABOX_DATABASE_DB=postgres'
            ]

            self.config.add('services.postgres', {
                'image': '%s/postgres:%s' % (self.args.docker_registry, self.args.version),
                'networks': ['infrabox'],
                'restart': 'always'
            })

            self.config.append('services.docker-registry-auth.links', ['postgres'])
            self.config.append('services.scheduler.links', ['postgres'])
            self.config.append('services.api.links', ['postgres'])

        self.config.append('services.api.environment', env)
        self.config.append('services.scheduler.environment', env)
        self.config.append('services.docker-registry-auth.environment', env)

        if self.args.gerrit_enabled:
            self.config.append('services.gerrit-trigger.environment', env)

    def main(self):
        Installer.copy_files(self.args, 'compose')
        self.args.root_url = 'http://localhost:8090'

        compose_path = os.path.join(self.args.o, 'compose', 'docker-compose.yml')
        self.config.load(compose_path)
        self.setup_rsa()
        self.setup_scheduler()
        self.setup_database()
        self.setup_docker_registry()
        self.setup_ldap()
        self.setup_nginx_ingress()
        self.setup_api()
        self.setup_job_git()
        self.setup_gerrit()
        self.config.dump(compose_path)
