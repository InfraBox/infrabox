FROM alpine:3.6

RUN apk add --no-cache python py2-psycopg2 py2-pip gcc openssl libffi-dev python2-dev musl-dev py2-flask git openssl-dev py2-requests && \
    pip install bcrypt PyJWT jsonschema cryptography flask-restplus eventlet flask_socketio boto3 google-cloud-storage future && \
    apk del gcc python2-dev musl-dev openssl-dev libffi-dev

ENV PYTHONPATH=/

COPY src/api /api
COPY src/pyinfraboxutils /pyinfraboxutils
COPY src/pyinfrabox /pyinfrabox
COPY infrabox/test/utils/id_rsa /var/run/secrets/rsa/id_rsa
COPY infrabox/test/utils/id_rsa.pub /var/run/secrets/rsa/id_rsa.pub

CMD python /api/server.py
