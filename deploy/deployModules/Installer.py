import base64
import logging
import os
import shutil
import stat
import sys
import yaml


class Installer(object):
    args = None
    logger = None

    def __init__(self, args):
        self.args = args
        logging.basicConfig(
            format='%(asctime)s,%(msecs)d %(levelname)-8s [%(filename)s:%(lineno)d] %(message)s',
            datefmt='%d-%m-%Y:%H:%M:%S',
            level=logging.WARN
        )

        self.logger = logging.getLogger("install")

    @staticmethod
    def check_file_exists(p):
        if not os.path.exists(p):
            print("%s does not exist" % p)
            sys.exit(1)

    @staticmethod
    def write_executable_file(path, content):
        os.makedirs(os.path.dirname(path))

        with open(path, 'w') as outfile:
            outfile.write(content)

        st = os.stat(path)
        os.chmod(path, st.st_mode | stat.S_IEXEC)

    @staticmethod
    def copy_files(args, directory):
        dir_path = os.path.dirname(os.path.realpath(__file__))
        chart_dir = os.path.join(dir_path, directory)
        target_path = os.path.join(args.o, directory)
        shutil.copytree(chart_dir, target_path)

    def getLogger(self):
        return self.logger

    def is_master(self):
        return self.args.cluster_name == "master"

    def required_option(self, name):
        args = vars(self.args)
        m = name.replace("-", "_")
        if not args.get(m, None):
            print("--%s not set" % name)
            sys.exit(1)

    def create_secret(self, name, namespace, data):
        secrets_dir = os.path.join(self.args.o, 'infrabox', 'templates', 'secrets')

        if not os.path.exists(secrets_dir):
            os.mkdir(secrets_dir)

        d = {}

        for k, v in data.iteritems():
            d[k] = base64.b64encode(v)

        s = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": name,
                "namespace": namespace
            },
            "type": "Opaque",
            "data": d
        }

        o = os.path.join(secrets_dir, namespace + '-' + name + '.yaml')
        with open(o, 'w') as outfile:
            yaml.dump(s, outfile, default_flow_style=False)
