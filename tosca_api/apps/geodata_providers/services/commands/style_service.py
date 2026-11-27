from django.core.exceptions import ValidationError
from django.utils import timezone

from ...engine_factory import EngineClientFactory
from ...models import Style
from .style_validation_service import StyleValidationService

UNSUPPORTED_MESSAGE = 'Remote style sync is not supported by this provider type.'


class StyleService:
    """Saves style content and pushes it to the style's engine.

    The Django admin (``StyleAdmin``) and the MCP server use the same steps:
    validate the content, save it locally, then upload it to GeoServer and
    record the remote state.
    """

    @classmethod
    def update_content(
        cls,
        style: Style,
        *,
        content: str | None = None,
        title: str | None = None,
        description: str | None = None,
    ) -> dict:
        """Validate and save new content / title / description, then sync the engine.

        Returns ``{'success': False, 'errors': [...]}`` without saving when the
        content is invalid, otherwise the result of :meth:`sync_remote`.
        """
        if content is not None:
            validation = StyleValidationService.validate(content=content, style_format=style.format)
            if not validation.get('valid'):
                return {'success': False, 'saved': False, 'errors': validation.get('errors', [])}
            style.file_content = content
            style.validation_state = Style.ValidationState.VALID
            style.validation_errors = []
        if title is not None:
            style.title = title
        if description is not None:
            style.description = description
        if style.geodata_engine.engine_type == 'geoserver':
            style.remote_state = Style.RemoteState.LOCAL_ONLY
            style.remote_error = ''
        try:
            style.save()
        except ValidationError as exc:
            return {'success': False, 'saved': False, 'errors': exc.messages}
        return {'saved': True, **cls.sync_remote(style)}

    @classmethod
    def sync_remote(cls, style: Style) -> dict:
        """Upload the saved style to its engine and record the outcome on the style."""
        if style.geodata_engine.engine_type != 'geoserver':
            style.remote_state = Style.RemoteState.UNSUPPORTED
            style.remote_error = UNSUPPORTED_MESSAGE
            style.sync_state = 'UNKNOWN'
            style.last_sync_error = style.remote_error
            style.save(update_fields=['remote_state', 'remote_error', 'sync_state', 'last_sync_error', 'updated_at'])
            return {'success': True, 'supported': False, 'message': UNSUPPORTED_MESSAGE}

        client = EngineClientFactory.create_client(style.geodata_engine)
        result = client.upload_style(
            name=style.name,
            content=style.file_content,
            style_format=style.format,
            workspace=style.workspace.name if style.workspace else None,
            overwrite=True,
        )
        if result.get('success'):
            now = timezone.now()
            style.remote_state = Style.RemoteState.SYNCED
            style.remote_error = ''
            style.remote_uploaded_at = now
            style.remote_verified_at = now
            style.sync_state = 'SYNCED'
            style.last_sync_at = now
            style.last_sync_error = ''
            style.remote_identifier = style.qualified_name
            style.save(update_fields=[
                'remote_state', 'remote_error', 'remote_uploaded_at',
                'remote_verified_at', 'sync_state', 'last_sync_at',
                'last_sync_error', 'remote_identifier', 'updated_at',
            ])
            return {'success': True, 'supported': True, 'message': 'Style uploaded to GeoServer and verified.'}

        style.remote_state = Style.RemoteState.FAILED
        style.remote_error = result.get('error') or result.get('message', 'Remote sync failed.')
        style.sync_state = 'FAILED'
        style.last_sync_at = timezone.now()
        style.last_sync_error = style.remote_error
        style.save(update_fields=[
            'remote_state', 'remote_error', 'sync_state', 'last_sync_at',
            'last_sync_error', 'updated_at',
        ])
        return {'success': False, 'supported': True, 'error': style.remote_error}
