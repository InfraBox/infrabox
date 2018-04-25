import os

from flask import jsonify
from flask_restplus import Resource

from pyinfraboxutils.ibrestplus import api

settings_ns = api.namespace('api/v1/settings',
                   description="Api settings")

@settings_ns.route('/')
class Settings(Resource):

    def get(self):
        o = {
            'INFRABOX_GITHUB_ENABLED': os.environ['INFRABOX_GITHUB_ENABLED'] == 'true',
            'INFRABOX_GITHUB_LOGIN_ENABLED': os.environ['INFRABOX_GITHUB_LOGIN_ENABLED'] == 'true',

            'INFRABOX_GITLAB_ENABLED': os.environ['INFRABOX_GITLAB_ENABLED'] == 'true',
            'INFRABOX_GITLAB_LOGIN_ENABLED': os.environ['INFRABOX_GITLAB_LOGIN_ENABLED'] == 'true',

            'INFRABOX_GERRIT_ENABLED': os.environ['INFRABOX_GERRIT_ENABLED'] == 'true',
            'INFRABOX_ACCOUNT_SIGNUP_ENABLED': os.environ['INFRABOX_ACCOUNT_SIGNUP_ENABLED'] == 'true',
            'INFRABOX_ACCOUNT_LDAP_ENABLED': os.environ['INFRABOX_ACCOUNT_LDAP_ENABLED'] == 'true',
            'INFRABOX_ROOT_URL': os.environ['INFRABOX_ROOT_URL']
        }

        return jsonify(o)
