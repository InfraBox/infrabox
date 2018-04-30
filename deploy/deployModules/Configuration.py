import yaml


class Configuration(object):
    def __init__(self):
        self.config = {}

    def add(self, name, value):
        elements = name.split('.')

        c = self.config
        for e in elements[:-1]:
            if e not in c:
                c[e] = {}

            c = c[e]

        c[elements[-1]] = value

    def append(self, name, values):
        elements = name.split('.')

        c = self.config
        for e in elements[:-1]:
            if e not in c:
                c[e] = {}

            c = c[e]

        k = elements[-1]
        if k not in c or not c[k]:
            c[k] = []

        c[k] += values

    def dump(self, p):
        with open(p, 'w') as outfile:
            yaml.dump(self.config, outfile, default_flow_style=False)

    def load(self, p):
        with open(p) as infile:
            self.config = yaml.load(infile)
