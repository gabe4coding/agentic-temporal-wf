FROM alpine:3.20

# Tinyproxy acts as a forward proxy between the sandbox-net (internal
# network, no NAT) and the outside world. It enforces an explicit FQDN
# allow-list via the `Filter` directive so a compromised agent cannot
# exfiltrate data to arbitrary domains.
RUN apk add --no-cache tinyproxy ca-certificates

COPY tinyproxy.conf /etc/tinyproxy/tinyproxy.conf
COPY filter /etc/tinyproxy/filter

EXPOSE 8888

CMD ["tinyproxy", "-d", "-c", "/etc/tinyproxy/tinyproxy.conf"]
