from whitenoise.storage import CompressedManifestStaticFilesStorage


class StaticStorage(CompressedManifestStaticFilesStorage):
    """Don't crash pages when a referenced static file (e.g. the logo) is missing.

    The hashed URL is used when the file exists; otherwise the plain URL is
    returned (it 404s quietly and the template hides the broken image).
    """

    manifest_strict = False

    def url(self, name, force=False):
        try:
            return super().url(name, force)
        except ValueError:
            return self.base_url + name
