require 'fluent/output'

module Fluent
  class SomeOutput < Output
    # First, register the plugin. NAME is the name of this plugin
    # and identifies the plugin in the configuration file.
    Fluent::Plugin.register_output('infrabox', self)

    # This method is called before starting.
    def configure(conf)
      super
    end

    # This method is called when starting.
    def start
      super
    end

    # This method is called when shutting down.
    def shutdown
      super
    end

    # This method is called when an event reaches Fluentd.
    # 'es' is a Fluent::EventStream object that includes multiple events.
    # You can use 'es.each {|time,record| ... }' to retrieve events.
    # 'chain' is an object that manages transactions. Call 'chain.next' at
    # appropriate points and rollback if it raises an exception.
    #
    # NOTE! This method is called by Fluentd's main thread so you should not write slow routine here. It causes Fluentd's performance degression.
    def emit(tag, es, chain)
      uri = URI.parse("http://infrabox-api.infrabox-system:8080/internal/logs/")
      r = /ib-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-([0-9a-zA-Z]+)/

      chain.next

      es.each {|time,record|
          ns = record['kubernetes']['namespace_name']
          m = r.match(ns)

          unless m
              return
          end

          log.info ns

          msg = {
              'time' => time,
              'job_id' => m[1],
              'extension_name' => m[2],
              'container_name' => record['kubernetes']['container_name'],
              'pod_name' => record['kubernetes']['pod_name'],
              'log' => record['log']
          }

          req = Net::HTTP::Post.new('/internal/logs/')
          req.body = msg.to_json
          req['Content-Type'] = 'application/json'

          http = Net::HTTP.new(uri.host, uri.port)
          http.request(req)

      }
    end
  end
end
