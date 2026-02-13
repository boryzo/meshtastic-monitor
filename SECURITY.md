# Security Policy

## Supported Versions

Currently supported versions for security updates:

| Version | Supported          |
| ------- | ------------------ |
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

If you discover a security vulnerability in Meshtastic Monitor, please report it by:

1. **Do NOT** open a public issue
2. Email the maintainer or create a private security advisory on GitHub
3. Include details:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if any)

We will acknowledge receipt within 48 hours and provide an estimated timeline for a fix.

## Security Considerations

### Authentication

**Important:** Meshtastic Monitor does **NOT** include built-in authentication or authorization.

- The application exposes a web UI and JSON API with no login requirement
- Anyone with network access can view data, send messages, and modify configuration
- **Run only on trusted networks** (e.g., home LAN, VPN)
- **DO NOT** expose to the public internet without additional security measures (reverse proxy with authentication, VPN, firewall rules)

### API Keys and Secrets

- API keys for SMS relay are transmitted via HTTPS POST with JSON body (not URL query parameters)
- Configuration files (`meshmon.ini`) may contain sensitive data - restrict file permissions:
  ```bash
  chmod 600 meshmon.ini
  ```
- The `/api/device/config` endpoint redacts PSKs by default
  - Use `?includeSecrets=1` only when necessary and understand the risk

### Network Security

- **SSL/TLS Verification**: Enabled by default for HTTPS connections (mesh status, SMS gateway)
- **TCP Relay**: Binding to `0.0.0.0` exposes the relay to your LAN - use firewall rules to restrict access
- Use HTTPS when accessing the web UI over untrusted networks

### Database

- SQLite database (`meshmon.db`) stores message history and node data
- No encryption at rest - protect the database file with filesystem permissions
- Consider the privacy implications of storing mesh traffic

### Input Validation

Recent improvements include:
- Strict validation of SQL DDL operations to prevent injection
- Specific exception handling instead of broad catch-all blocks
- Input sanitization for user-provided configuration values

## Security Best Practices

1. **Network Isolation**
   - Run on a private network segment
   - Use VPN for remote access
   - Configure firewall rules to restrict access

2. **Configuration Security**
   - Protect `meshmon.ini` with appropriate file permissions
   - Rotate SMS API keys regularly
   - Use strong, unique credentials for any SMS gateway

3. **Monitoring**
   - Review logs (`meshmon.log`) for suspicious activity
   - Monitor API access patterns
   - Check for unauthorized configuration changes

4. **Updates**
   - Keep Meshtastic Monitor updated to the latest version
   - Subscribe to security advisories
   - Update Python dependencies regularly:
     ```bash
     pip install --upgrade meshtastic-monitor
     ```

5. **Deployment**
   - Use a reverse proxy (nginx, Caddy) with authentication for internet exposure
   - Consider rate limiting on the TCP relay and SMS relay
   - Run with minimal privileges (non-root user)

## Known Limitations

- No built-in rate limiting
- No built-in authentication or authorization
- No built-in audit logging
- SQLite database is not encrypted at rest

These are design decisions for simplicity and ease of deployment on trusted networks.

## Security Updates

Security fixes will be released as soon as possible after verification. Check:
- GitHub releases page
- Project README
- Git commit history for security-related changes

## Contact

For security-related questions or concerns, please contact the maintainers through:
- GitHub Issues (for general questions)
- GitHub Security Advisories (for vulnerability reports)
