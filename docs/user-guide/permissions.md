# Roles, permissions, and organization scope

[User-guide home](README.md) · [Campaigns](campaigns.md) · [Geodata administration](geodata.md) · [Troubleshooting](troubleshooting.md)

TOSCA authentication and top-level administrative access come from Keycloak. Django synchronizes the user's staff and superuser flags from authoritative Keycloak roles during authentication.

## Main access levels

| Access | Typical capabilities |
|---|---|
| Authenticated content editor | Work with objects allowed by the user's organization and assigned Django permissions. |
| Django staff (`DJANGO_STAFF`) | Enter the Django admin and use permitted administration sections. |
| Django superadministrator (`DJANGO_SUPERADMIN`) | Full Django administrative access, including platform-level configuration. This role also implies staff access. |
| GeoServer administrator | Administer GeoServer itself. A GeoServer `ADMIN` role alone does not grant Django staff access. |

Exact model permissions may further restrict viewing, adding, changing, deleting, publishing, or synchronizing objects.

## Organization scope

- Campaigns and Workspaces belong to an Organization.
- Editors should work only within their authorized organizations.
- A Geodata Engine may be unrestricted or limited to selected organizations.
- Workspace ownership contributes to GeoServer access-control roles.
- Shared registries, such as taxonomy definitions, may be visible but protected from modification by organization administrators.

## If an admin section or object is missing

1. Sign out and sign in again so current Keycloak roles are synchronized.
2. Confirm you are using the correct account and organization.
3. Ask an administrator to check the Keycloak role and Django model permission—not to create a duplicate object.
4. For geodata, confirm the engine is active and available to your organization.
5. Record the page, action, object, and time when reporting the problem. Do not send passwords or access tokens.

## Security rules for editors

- Never share accounts or credentials.
- Do not place private personal information in public fields.
- Do not copy production credentials into screenshots or support tickets.
- Use the least privileged role that permits the task.
- Treat permission errors as access-control signals, not obstacles to bypass.

