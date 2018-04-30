import argparse
import os
import sys
from deployModules import DockerCompose, Kubernetes


def option_not_supported(args, name):
    args = vars(args)
    m = name.replace("-", "_")
    if args.get(m, None):
        print("--%s not supported" % name)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description='Install InfraBox')
    parser.add_argument('-o',
                        required=True,
                        help="Output directory in which the configuration should be stored")

    # Platform
    parser.add_argument('--platform',
                        choices=['docker-compose', 'kubernetes'],
                        required=True)
    parser.add_argument('--version', default='latest')
    parser.add_argument('--root-url')
    parser.add_argument('--docker-registry', default='quay.io/infrabox')

    # Cluster
    parser.add_argument('--cluster-name', default='master')
    parser.add_argument('--cluster-labels')

    # Admin config
    parser.add_argument('--admin-email')
    parser.add_argument('--admin-password')

    # General
    parser.add_argument('--general-dont-check-certificates', action='store_true', default=False)
    parser.add_argument('--general-worker-namespace', default='infrabox-worker')
    parser.add_argument('--general-system-namespace', default='infrabox-system')
    parser.add_argument('--general-rsa-public-key')
    parser.add_argument('--general-rsa-private-key')
    parser.add_argument('--general-rbac-disabled', action='store_true', default=False)
    parser.add_argument('--general-report-issue-url', default='https://github.com/InfraBox/infrabox/issues')

    # Database configuration
    parser.add_argument('--database',
                        choices=['postgres', 'cloudsql'],
                        help='Which kind of postgres database you want to use')

    parser.add_argument('--postgres-host')
    parser.add_argument('--postgres-port', default=5432, type=int)
    parser.add_argument('--postgres-username')
    parser.add_argument('--postgres-password')
    parser.add_argument('--postgres-database')

    parser.add_argument('--cloudsql-instance-connection-name')
    parser.add_argument('--cloudsql-proxy-service-account-key-file')
    parser.add_argument('--cloudsql-proxy-username')
    parser.add_argument('--cloudsql-proxy-password')

    # Storage configuration
    parser.add_argument('--storage',
                        choices=['s3', 'gcs'],
                        help='Which kind of storage you want to use')

    parser.add_argument('--s3-access-key')
    parser.add_argument('--s3-secret-key')
    parser.add_argument('--s3-region')
    parser.add_argument('--s3-endpoint')
    parser.add_argument('--s3-port', default=443, type=int)
    parser.add_argument('--s3-bucket', default='infrabox')
    parser.add_argument('--s3-secure', default='true')

    parser.add_argument('--gcs-service-account-key-file')
    parser.add_argument('--gcs-bucket')

    # Scheduler
    parser.add_argument('--scheduler-disabled', action='store_true', default=False)

    # LDAP
    parser.add_argument('--ldap-enabled', action='store_true', default=False)
    parser.add_argument('--ldap-dn')
    parser.add_argument('--ldap-password')
    parser.add_argument('--ldap-base')
    parser.add_argument('--ldap-url')

    # Gerrit
    parser.add_argument('--gerrit-enabled', action='store_true', default=False)
    parser.add_argument('--gerrit-hostname')
    parser.add_argument('--gerrit-port')
    parser.add_argument('--gerrit-username')
    parser.add_argument('--gerrit-private-key')
    parser.add_argument('--gerrit-review-enabled', action='store_true', default=False)

    # Github
    parser.add_argument('--github-enabled', action='store_true', default=False)
    parser.add_argument('--github-client-secret')
    parser.add_argument('--github-client-id')
    parser.add_argument('--github-webhook-secret')
    parser.add_argument('--github-api-url', default='https://api.github.com')
    parser.add_argument('--github-login-enabled', action='store_true', default=False)
    parser.add_argument('--github-login-url', default='https://github.com/login')
    parser.add_argument('--github-login-allowed-organizations', default="")

    # TLS
    parser.add_argument('--ingress-tls-disabled', action='store_true', default=False)
    parser.add_argument('--ingress-tls-host')
    parser.add_argument('--ingress-tls-dont-force-redirect', action='store_true', default=False)

    # Account
    parser.add_argument('--account-signup-enabled', action='store_true', default=False)

    # Local Cache
    parser.add_argument('--local-cache-enabled', action='store_true', default=False)
    parser.add_argument('--local-cache-host-path')

    # Job
    parser.add_argument('--job-mount-docker-socket', action='store_true', default=False)
    parser.add_argument('--job-use-host-docker-daemon', action='store_true', default=False)
    parser.add_argument('--job-security-context-capabilities-enabled', action='store_true', default=False)

    # Parse options
    args = parser.parse_args()
    if os.path.exists(args.o):
        print("%s does already exist" % args.o)
        sys.exit(1)

    if args.platform == 'docker-compose':
        d = DockerCompose.DockerCompose(args)
        d.main()
    elif args.platform == 'kubernetes':
        k = Kubernetes.Kubernetes(args)
        k.main()
    else:
        raise Exception("unknown platform")


if __name__ == '__main__':
    main()
