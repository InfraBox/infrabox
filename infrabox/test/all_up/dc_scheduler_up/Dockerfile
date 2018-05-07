FROM docker:17.06

RUN apk add --no-cache python2 py2-requests py2-yaml py2-psycopg2 py2-jwt py2-cryptography

COPY src/scheduler/docker-compose scheduler
COPY src/pyinfraboxutils /pyinfraboxutils
COPY infrabox/test/utils/id_rsa /var/run/secrets/rsa/id_rsa
COPY infrabox/test/utils/id_rsa.pub /var/run/secrets/rsa/id_rsa.pub
COPY infrabox/test/all_up/dc_scheduler_up/daemon.json /etc/docker/daemon.json
COPY infrabox/test/all_up/dc_scheduler_up/daemon.json /etc/default/docker.json

ENV PYTHONPATH=/

ENTRYPOINT python scheduler/scheduler.py
