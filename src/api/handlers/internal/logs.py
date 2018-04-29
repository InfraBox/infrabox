from flask import request, g
from flask_restplus import Resource, fields

from pyinfraboxutils.ibflask import OK
from pyinfraboxutils.ibrestplus import api

from api.namespaces import internal as ns

log_entry_model = api.model('LogEntry', {
    'log': fields.String(required=True),
    'extension_name': fields.String(required=True),
    'pod_name': fields.String(required=True),
    'container_name': fields.String(required=True),
    'time': fields.Integer(required=True),
    'job_id': fields.String(required=True)
})

@ns.route('/logs/')
class Collaborators(Resource):

    @api.expect(log_entry_model)
    def post(self):
        b = request.get_json()

        g.db.execute("""
            INSERT INTO container_logs(job_id, extension_name, time, log, pod_name, container_name)
            VALUES (%s, %s, to_timestamp(%s), %s, %s, %s)
        """, [b['job_id'], b['extension_name'], b['time'], b['log'],
              b['pod_name'], b['container_name']])

        g.db.commit()
        return OK('Successfully stored log')
