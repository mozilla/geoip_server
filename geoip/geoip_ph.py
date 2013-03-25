"""
    This Source Code Form is subject to the terms of the Mozilla Public
    License, v. 2.0. If a copy of the MPL was not distributed with this
    file, You can obtain one at http://mozilla.org/MPL/2.0/.


    Stand Alone GeoIP lookup service.

    This code is based off of the django.geoip lookup module:
    https://docs.djangoproject.com/en/dev/ref/contrib/gis/geoip/#module-django.contrib.gis.geoip

    Since this service will be installed to a distributed, production server
    and since updates may introduce corruption to the dataset that may
    cause the C API to segfault and kill the python app, this code creates
    a separate service that isolates such faults.

    By default, this application runs at localhost on port 5309
    and uses the 0MQ library

    send:
    GET <addr>\n

    receive:
    On success
    {'success': {<GeoIP information as JSON>}}\n

    On error:
    {'error': <Error description string>}\n

"""
import json
import logging
import settings
from base import GeoIPException, GeoIP
from statsd import statsd


class GeoServer():

    def __init__(self, **kw):
        self.config = settings

        self.log = logging.getLogger()
        self.log.info('Starting GeoServer instance...')
        try:
            self.geoip = GeoIP(path=self.config.GEOIP_PATH,
                               cache=GeoIP.GEOIP_MEMORY_CACHE)
        except GeoIPException as e:
            self.log.error("Could not start GeoIP server: %s", str(e))

    def _error(self, errstr):
        if hasattr(self.config, 'DEBUG'):
            self.log.error("GEOIP: %s" % errstr)
        return self._return('error', errstr)

    def _return(self, label='reply', obj={}):
        reply = json.dumps({label: obj})
        if hasattr(self.config, 'DEBUG'):
            self.log.info("Returning: %s" % reply)
        return "%s\n" % reply

    def checkConnection(self):
        try:
            if self.geoip.country_code('mozilla.org') == 'US':
                return "200 OK\n"
        except Exception as e:
            self.log.error("Failed Health Check: %s", str(e))
            return "500 Error\n"

    def process(self, job):
        line = job.data
        if statsd is not None:
            timer = statsd.Timer('GeoIP')
            req_counter = statsd.counter('GeoIP.request')
            success_counter = statsd.counter('GeoIP.success')
            fail_counter = statsd.counter('GeoIP.failure')
            timer.start()
        else:
            timer = None
            req_counter = 0
            success_counter = 0
            fail_counter = 0
        try:
            if 'monitor' == line.strip():
                return self.checkConnection()
            if ' ' not in line:
                return self._error('Invalid request. Try "GET addr"')
            items = line.split(' ')
            cmd = items[0]
            addr = items[1].strip().replace('/', '')
            if cmd.upper() != 'GET':
                return self._error('Invalid command. Try "GET addr"')
            if addr is None:
                return self._error('Missing address')
            try:
                req_counter += 1
                reply = self.geoip.city(addr)
                if reply is None:
                    fail_counter += 1
                    return self._error('No information for site')
                success_counter += 1
                reply.update({'addr': addr})
                return self._return('success', reply)
            except Exception as e:
                fail_counter += 1
                return self._error('Unknown Exception "%s"' % str(e))
        finally:
            if timer:
                timer.stop('Request')


geo = GeoServer()


def lookup(data):
    return geo.process(data)


if __name__ == "__main__":
    try:
        """
        Statsd is HEAVILY django dependent. The following
        work around attempts to address that.
        """
        import os
        for i in ['STATSD_HOST', 'STATSD_PORT', 'STATSD_PREFIX']:
            if hasattr(settings, i):
                print 'loading %s = %s' % (i, getattr(settings, i))
                os.environ[i] = str(getattr(settings, i))
    except ImportError as e:
        print "Cannot run. Terminating."
        print str(e)
        exit(-1)
