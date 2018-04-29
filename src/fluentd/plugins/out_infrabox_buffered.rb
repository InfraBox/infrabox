require 'fluent/output'
require 'net/http'
require 'uri'
require 'json'

module Fluent
  class InfraBoxOutput < BufferedOutput
    Fluent::Plugin.register_output('infrabox', self)

    # config_param defines a parameter. You can refer a parameter via @path instance variable
    # Without :default, a parameter is required.
    # config_param :path, :string

    # This method is called before starting.
    # 'conf' is a Hash that includes configuration parameters.
    # If the configuration is invalid, raise Fluent::ConfigError.
    def configure(conf)
      super

      # You can also refer raw parameter via conf[name].
      # @path = conf['path']
    end

    # This method is called when starting.
    # Open sockets or files here.
    def start
      super
    end

    # This method is called when shutting down.
    # Shutdown the thread and close sockets or files here.
    def shutdown
      super
    end

    # This method is called when an event reaches to Fluentd.
    # Convert the event to a raw string.
    def format(tag, time, record)
      [tag, time, record].to_msgpack
    end

    # This method is called every flush interval. Write the buffer chunk
    # to files or databases here.
    # 'chunk' is a buffer chunk that includes multiple formatted
    # events. You can use 'data = chunk.read' to get all events and
    # 'chunk.open {|io| ... }' to get IO objects.
    # Optionally, you can use chunk.msgpack_each to deserialize objects.
    def write(chunk)
      uri = URI.parse("http://infrabox-api.infrabox-system:8080/internal/logs/")
      r = /ib-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})-([0-9a-zA-Z]+)/

      chunk.msgpack_each {|(tag,time,record)|
          ns = record['kubernetes']['namespace_name']
          m = r.match(ns)
          log.info ns
          log.info record['log']

          unless m
              return
          end

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
