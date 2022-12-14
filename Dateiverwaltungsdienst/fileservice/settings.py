"""
Django settings for fileservice project.

Generated by 'django-admin startproject' using Django 4.1.3.

For more information on this file, see
https://docs.djangoproject.com/en/4.1/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/4.1/ref/settings/
"""
import os
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.1/howto/deployment/checklist/

SECRET_KEY = os.environ.get("SECRET_KEY")

DEBUG = "DEBUG" in os.environ and str(os.environ["DEBUG"]).lower() == "true"

ALLOWED_HOSTS = ["127.0.0.1"]


# Application definition

INSTALLED_APPS = [
    "app_file",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "fileservice.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "fileservice.wsgi.application"


# Database
# https://docs.djangoproject.com/en/4.1/ref/settings/#databases

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "HOST": os.environ["POSTGRES_HOST"],
        "PORT": os.environ["POSTGRES_PORT"],
        "NAME": os.environ["POSTGRES_DATABASE"],
        "USER": os.environ["POSTGRES_USERNAME"],
        "PASSWORD": os.environ["POSTGRES_PASSWORD"],
        "OPTIONS": {
            "sslmode": "verify-full",
            "sslrootcert": os.environ["POSTGRES_SSL_CA_PATH"] if "POSTGRES_SSL_CA_PATH" in os.environ else os.path.join("/", "etc", "patientenakte", "ssl", "db-ca-cert.pem")
        }
    }
}

MESSAGE_QUEUES = {
    "user_update": {
        "host": os.environ["USER_UPDATE_QUEUE_HOST"],
        "port": os.environ["USER_UPDATE_QUEUE_PORT"],
        "username": os.environ["USER_UPDATE_QUEUE_USERNAME"],
        "password": os.environ["USER_UPDATE_QUEUE_PASSWORD"],
        "exchange_name": os.environ["USER_UPDATE_QUEUE_EXCHANGE_NAME"]
    }
}


# Password validation
# https://docs.djangoproject.com/en/4.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.1/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.1/howto/static-files/

STATIC_URL = "static/"

# Default primary key field type
# https://docs.djangoproject.com/en/4.1/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

APPEND_SLASH = False

SIMPLE_JWT = {
    "ALGORITHM": "RS512",
    'VERIFYING_KEY': open(BASE_DIR.__str__() + "/config/jwt-signature-rsa-public.pem", "rb").read(),
}

# Note: Handling large files in memory is non-advisable, because it makes the server
# far more susceptible to DoS attacks, especially though slow uploads. Django does
# provide a meacanism to save large requests to a file instead. However, the file
# service currently saves the user data within the relational db using the Django
# model system (rather than using model FileFields), by writing a bytestring to a
# model field. Doing so requires the file to be in memory anyway.
# When changing to a more suitable file storage, large files should be streamed to
# that storage rather than being loaded to memory and this setting should be
# significantly reduced, to allow loading reasonably small files to memory only.
DATA_UPLOAD_MAX_MEMORY_SIZE = 128 * 1024 * 1024
