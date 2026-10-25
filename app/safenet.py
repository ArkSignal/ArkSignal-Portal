"""Outgoing requests to an address a person typed (a webhook, an AirTrail server): the site makes them on that person's behalf, so they must never reach the site's own network or the machine it runs on.

The rule is applied where the connection is made, not before it: the name is resolved once, every address it gives must be a public one (not private, loopback, link-local, shared (100.64/10), reserved, multicast or
unspecified, and an IPv4 address inside an IPv6 one counts as the IPv4 address), and the connection goes to the address that was checked. A name that answers with a public address first and a private one the next
time (DNS rebinding) therefore gets nothing. Redirects are not followed. `allow_private` is the administrator's own switch for sites whose AirTrail or webhook lives on the home network."""
import http.client
import ipaddress
import socket
import urllib.request


class Blocked(OSError):
    """The address is not one the site may contact."""


def check_ip(text, allow_private=False):
    ip = ipaddress.ip_address(text.split("%")[0])
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    if not allow_private and not ip.is_global:
        raise Blocked("that address is on a private network")
    if ip.is_multicast or ip.is_unspecified:
        raise Blocked("that is not an address a server answers on")
    return str(ip)


def resolve(host, port, allow_private=False):
    """The address to connect to (the first of the name's addresses): every address of the name is checked, and one that is not allowed refuses the name."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise Blocked("that address could not be found")
    ips = [i[4][0] for i in infos]
    if not ips:
        raise Blocked("that address could not be found")
    for ip in ips:
        check_ip(ip, allow_private)
    return ips[0]


class _Pinned:
    ALLOW = False

    def _pinned_socket(self):
        ip = resolve(self.host, self.port, self.ALLOW)
        return socket.create_connection((ip, self.port), self.timeout, self.source_address)


def opener(allow_private=False):
    """A urllib opener that connects only to checked addresses and follows no redirect."""
    class Plain(_Pinned, http.client.HTTPConnection):
        ALLOW = allow_private

        def connect(self):
            self.sock = self._pinned_socket()

    class Secure(_Pinned, http.client.HTTPSConnection):
        ALLOW = allow_private

        def connect(self):
            sock = self._pinned_socket()
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)

    class HttpHandler(urllib.request.HTTPHandler):
        def http_open(self, req):
            return self.do_open(Plain, req)

    class HttpsHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(Secure, req, context=self._context)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    return urllib.request.build_opener(HttpHandler, HttpsHandler, NoRedirect)
