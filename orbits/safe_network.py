"""HTTPS-only transport with public-address DNS pinning and checked redirects."""
import http.client
import ipaddress
import socket
import ssl
import urllib.parse
import urllib.request


def validate_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() != 'https' or not parsed.hostname or parsed.username is not None or parsed.password is not None:
        raise ValueError('Remote content requires HTTPS without embedded credentials')
    if parsed.hostname.rstrip('.').lower() == 'localhost' or '%' in parsed.hostname:
        raise ValueError('Local network destinations are blocked')
    try:
        port = parsed.port or 443
    except ValueError as error:
        raise ValueError('Invalid HTTPS port') from error
    return parsed.hostname, port


def public_addresses(host, port):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise OSError('The remote host has no usable addresses')
    for family, socktype, protocol, _name, address in addresses:
        ip = ipaddress.ip_address(address[0])
        ip = ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip
        if not ip.is_global or ip.is_reserved or ip.is_multicast or getattr(ip, 'is_site_local', False):
            raise ValueError('Private, local and reserved network destinations are blocked')
    return addresses


def _connect_public(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    host, port = address
    error = None
    for family, kind, protocol, _name, sockaddr in public_addresses(host, port):
        connection = socket.socket(family, kind, protocol)
        try:
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                connection.settimeout(timeout)
            if source_address:
                connection.bind(source_address)
            # Connect the validated numeric sockaddr directly: do not resolve the name twice.
            connection.connect(sockaddr)
            return connection
        except OSError as failure:
            connection.close()
            error = failure
    raise error or OSError('Could not connect to remote host')


class PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_public

    def set_tunnel(self, *args, **kwargs):
        raise ValueError('Proxy tunneling is not supported by protected downloads')


class PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        validate_url(request.full_url)
        return self.do_open(PublicHTTPSConnection, request, context=self._context)


class CheckedRedirect(urllib.request.HTTPRedirectHandler):
    max_redirections = 5
    max_repeats = 2

    def redirect_request(self, request, response, code, message, headers, newurl):
        validate_url(newurl)
        redirected = super().redirect_request(request, response, code, message, headers, newurl)
        if redirected and urllib.parse.urlsplit(request.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            for name in ('Authorization', 'Cookie', 'Proxy-Authorization'):
                redirected.remove_header(name)
        return redirected


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), PublicHTTPSHandler(context=ssl.create_default_context()), CheckedRedirect())


def open_remote(request, timeout=30):
    url = request.full_url if isinstance(request, urllib.request.Request) else request
    validate_url(url)
    return _opener.open(request, timeout=timeout)
