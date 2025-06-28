""" python deps for this project """

install_requires: list[str] = [
    "flask",
    "oauth2client",
    "bcrypt",
    "gunicorn",
    "cryptography",
    "pygooglecloud",
    # google modules
    "google-cloud-core",
    "google-cloud-quotas",
    "google-cloud-datastore",
    "google-cloud-service-usage",
    "google-cloud-resource-manager",
    "google-api-python-client",
    "google-auth",
]
build_requires: list[str] = [
    "pydmt",
    "pymakehelper",
    "pylint",
]
requires = install_requires + build_requires
