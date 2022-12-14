import json
import jwt
import logging
import re
import time

from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.db import connection, transaction
from django.db.utils import DatabaseError
from django.http import HttpRequest, HttpResponse
from typing import Any, Callable, List, Union

from .models import StorageUser, File, Directory, Share
from .serializers import serialize_user_info, serialize_storage_user, serialize_file, serialize_directory, serialize_share

LIMIT_SINGLE_FILE_SIZE = 128 * 1024 * 1024
LIMIT_USER_TOTAL_SIZE = 1024 * 1024 * 1024

STATUS_OK = ("ok", 200)
STATUS_CREATED = ("ok", 201)
STATUS_BAD_REQUEST = ("malformed_request", 400)
STATUS_UNAUTHORIZED = ("unauthorized", 401)
STATUS_NOT_FOUND = ("not_found", 404)
STATUS_PERMISSION_DENIED = ("permission_denied", 403)
STATUS_DUPLICATE_NAME = ("duplicate_name", 409)
STATUS_TRANSFERRAL_REJECTED = ("transferral_rejected", 422)
STATUS_CYCLIC_DIRECTORY_TREE = ("cycle_detected", 409)
STATUS_UNMOVABLE_DIRECTORY = ("unmovable_directory", 422)
STATUS_DIRECTORY_NOT_EMPTY = ("not_empty", 422)
STATUS_INVALID_SHARE_SUBJECT = ("invalid_subject", 422)
STATUS_INVALID_CONTACT = ("invalid_contact", 422)
STATUS_QUOTA_EXCEEDED = ("quota_exceeded", 413)
STAUTS_INTERNAL_ERROR = ("internal_error", 500)

AUTHORIZATION_HEADER_NAME = "Authorization"

CLAIM_USER_ID = "user_id"
CLAIM_USER_NAME = "user_name"
CLAIM_ISSUED_AT = "iat"
CLAIM_EXPIRES_AT = "exp"
CLAIM_TOKEN_TYPE = "token_type"

ACCESS_TOKEN_TYPE = "access"

class AbstractView:

    def __init__(self):
        self.method_by_verb: dict[str, Callable[[HttpRequest, Any], HttpResponse]] = {
            "GET": self._handle_get,
            "POST": self._handle_post,
            "PUT": self._handle_put,
            "DELETE": self._handle_delete
        }

    def handle(self, request: HttpRequest, **kwargs) -> HttpResponse:
        try:
            return self.method_by_verb.get(request.method, self._handle_unsupported_method)(request, **kwargs)
        except _ErrorBadRequest as e:
            return _response_for_json(STATUS_BAD_REQUEST, message=e.msg)
        except _ErrorUnauthorized:
            return _response_for_json(STATUS_UNAUTHORIZED)
        except _ErrorAccessDenied:
            return _response_for_json(STATUS_PERMISSION_DENIED)
        except _ErrorDuplicateName:
            return _response_for_json(STATUS_DUPLICATE_NAME)
        except _ErrorTransferralRejected:
            return _response_for_json(STATUS_TRANSFERRAL_REJECTED)
        except _ErrorCyclicDirectoryTree:
            return _response_for_json(STATUS_CYCLIC_DIRECTORY_TREE)
        except _ErrorUnmovableDirectory:
            return _response_for_json(STATUS_UNMOVABLE_DIRECTORY)
        except _ErrorDirectoryNotEmpty:
            return _response_for_json(STATUS_DIRECTORY_NOT_EMPTY)
        except _ErrorInvalidShareSubject:
            return _response_for_json(STATUS_INVALID_SHARE_SUBJECT)
        except _ErrorInvalidContact:
            return _response_for_json(STATUS_INVALID_CONTACT)
        except _ErrorQuotaExceeded:
            return _response_for_json(STATUS_QUOTA_EXCEEDED)
        except _ErrorNotFound:
            return _response_for_json(STATUS_NOT_FOUND)
        except ObjectDoesNotExist:
            return _response_for_json(STATUS_NOT_FOUND)
        except Exception as e:
            logging.exception(e)
            return _response_for_json(STAUTS_INTERNAL_ERROR)

    def _handle_unsupported_method(self, request):
        raise _ErrorBadRequest("Unsupported method: %s" % request.method)

    def _handle_get(self, request: HttpRequest, **kwargs) -> HttpResponse:
        raise _ErrorBadRequest("Unsupported method: GET")
    def _handle_post(self, request: HttpRequest, **kwargs) -> HttpResponse:
        raise _ErrorBadRequest("Unsupported method: POST")
    def _handle_put(self, request: HttpRequest, **kwargs) -> HttpResponse:
        raise _ErrorBadRequest("Unsupported method: PUT")
    def _handle_delete(self, request: HttpRequest, **kwargs) -> HttpResponse:
        raise _ErrorBadRequest("Unsupported method: DELETE")

class UserInfoView(AbstractView):

    def _handle_get(self, request: HttpRequest, **kwargs) -> HttpResponse:
        user_id = _get_kwarg(kwargs, "user_id", converter=int)
        user, _ = _verify_authenticated(request, user_id)
        _verify_can_access(user_id, user, is_write=False)
        return _response_for_json(STATUS_OK, userinfo=serialize_user_info(user))

class FileView(AbstractView):

    def _handle_get(self, request: HttpRequest, **kwargs) -> HttpResponse:
        _verify_kwargs(kwargs, ["file_id"])

        user_id = _get_query_param(request, "user", converter=int)
        _verify_authenticated(request, user_id)

        file_id = _get_kwarg(kwargs, "file_id", converter=int)
        file_obj = File.objects.get(id=file_id)
        _verify_can_access(user_id, file_obj, is_write=False)

        return _response_for_binary(STATUS_OK, file_obj.data)

    def _handle_post(self, request: HttpRequest, **kwargs) -> HttpResponse:
        _verify_kwargs(kwargs, [])

        user_id = _get_query_param(request, "user", converter=int)
        _verify_authenticated(request, user_id)

        file_name = _get_query_param(request, "name", converter=str)
        parent_directory_id = _get_query_param(request, "parentDirectory", converter=int)
        parent_directory = Directory.objects.get(id=parent_directory_id)
        _verify_can_access(user_id, parent_directory, is_write=True)
        _verify_unique_names(file_name, parent_directory)

        if len(request.body) > LIMIT_SINGLE_FILE_SIZE:
            raise _ErrorQuotaExceeded()

        with transaction.atomic():
            _verify_quota(user_id, len(request.body))
            file_obj = File.objects.create(name=file_name, owner=parent_directory.owner, parent_directory=parent_directory, data=request.body)

        return _response_for_json(STATUS_OK, file=serialize_file(file_obj))

    def _handle_put(self, request: HttpRequest, **kwargs) -> HttpResponse:
        _verify_kwargs(kwargs, ["file_id"])

        user_id = _get_query_param(request, "user", converter=int)
        _verify_authenticated(request, user_id)

        file_name = _get_query_param(request, "name", required=False, converter=str)
        parent_directory_id = _get_query_param(request, "parentDirectory", required=False, converter=int)
        write_body = _get_query_param(request, "writebody", required=False, converter=_get_converter_for_enum([""])) == ""
        if not write_body and len(request.body) > 0:
            raise _ErrorBadRequest("Cannot have a request body when not editing the file content")
        file_id = _get_kwarg(kwargs, "file_id", converter=int)
        file_obj = File.objects.get(id=file_id)
        _verify_can_access(user_id, file_obj, is_write=True)

        with transaction.atomic():
            if file_name is not None or parent_directory_id is not None:
                current_dir = file_obj.parent_directory
                _verify_can_access(user_id, current_dir, is_write=True)
                target_dir = current_dir
                if parent_directory_id is not None:
                    target_dir = Directory.objects.get(id=parent_directory_id)
                    _verify_can_access(user_id, target_dir, is_write=True)
                    _verify_same_owner(file_obj, current_dir, target_dir)
                    if current_dir != target_dir:
                        file_obj.parent_directory = target_dir
                if file_name is not None:
                    file_obj.name = file_name
                _verify_unique_names(file_obj, target_dir)
            if write_body:
                _verify_quota(user_id, len(request.body), file_obj.id)
                file_obj.data = request.body

            file_obj.save()

        return _response_for_json(STATUS_OK, file=serialize_file(file_obj))

    def _handle_delete(self, request: HttpRequest, **kwargs):
        _verify_kwargs(kwargs, ["file_id"])

        user_id = _get_query_param(request, "user", converter=int)
        _verify_authenticated(request, user_id)

        file_id = _get_kwarg(kwargs, "file_id", converter=int)
        file_obj = File.objects.get(id=file_id)
        _verify_can_access(user_id, file_obj, is_write=True)
        parent_directory = file_obj.parent_directory
        _verify_can_access(user_id, parent_directory, is_write=True)

        file_obj.delete()

        return _response_for_json(STATUS_OK)

class DirectoryView(AbstractView):

    def _handle_get(self, request: HttpRequest, **kwargs) -> HttpResponse:
        _verify_kwargs(kwargs, ["directory_id"])

        user_id = _get_query_param(request, "user", converter=int)
        _verify_authenticated(request, user_id)

        directory_id = _get_kwarg(kwargs, "directory_id", converter=int)
        directory = Directory.objects.get(id=directory_id)
        _verify_can_access(user_id, directory, is_write=False)

        return _response_for_json(STATUS_OK, directory=serialize_directory(directory, include_children=True))

    def _handle_post(self, request: HttpRequest, **kwargs) -> HttpResponse:
        _verify_kwargs(kwargs, [])

        user_id = _get_query_param(request, "user", converter=int)
        _verify_authenticated(request, user_id)

        directory_name = _get_query_param(request, "name", converter=str)
        parent_directory_id = _get_query_param(request, "parentDirectory", converter=int)
        parent_directory = Directory.objects.get(id=parent_directory_id)
        _verify_can_access(user_id, parent_directory, is_write=True)
        _verify_unique_names(directory_name, parent_directory)

        directory = Directory.objects.create(name=directory_name, owner=parent_directory.owner, parent=parent_directory)

        return _response_for_json(STATUS_OK, directory=serialize_directory(directory))

    def _handle_put(self, request: HttpRequest, **kwargs) -> HttpResponse:
        _verify_kwargs(kwargs, ["directory_id"])

        user_id = _get_query_param(request, "user", converter=int)
        _verify_authenticated(request, user_id)

        directory_name = _get_query_param(request, "name", required=False, converter=str)
        parent_directory_id = _get_query_param(request, "parentDirectory", required=False, converter=int)
        directory_id = _get_kwarg(kwargs, "directory_id", converter=int)
        directory = Directory.objects.get(id=directory_id)
        _verify_can_access(user_id, directory, is_write=True)

        with transaction.atomic():
            if directory_name is not None or parent_directory_id is not None:
                current_dir = directory.parent
                _verify_can_access(user_id, current_dir, is_write=True)
                target_dir = current_dir
                if parent_directory_id is not None:
                    target_dir = Directory.objects.get(id=parent_directory_id)
                    _verify_can_access(user_id, target_dir, is_write=True)
                    _verify_movable(directory, target_dir)
                    _verify_same_owner(directory, current_dir, target_dir)
                    if current_dir != target_dir:
                        directory.parent = target_dir
                if directory_name is not None:
                    directory.name = directory_name
                _verify_unique_names(directory, target_dir)

            directory.save()

        return _response_for_json(STATUS_OK, directory=serialize_directory(directory))

    def _handle_delete(self, request: HttpRequest, **kwargs):
        _verify_kwargs(kwargs, ["directory_id"])

        user_id = _get_query_param(request, "user", converter=int)
        _verify_authenticated(request, user_id)

        directory_id = _get_kwarg(kwargs, "directory_id", converter=int)
        directory = Directory.objects.get(id=directory_id)
        _verify_can_access(user_id, directory, is_write=True)
        parent_directory = directory.parent
        if parent_directory is None:
            raise _ErrorUnmovableDirectory()
        _verify_can_access(user_id, parent_directory, is_write=True)
        cascade = _get_query_param(request, "cascade", required=False, converter=_get_converter_for_enum([""])) == ""
        if cascade:
            with transaction.atomic():
                to_delete_list = [directory]
                while len(to_delete_list) > 0:
                    to_delete = to_delete_list.pop()
                    children = to_delete.directory_set.all()
                    if len(children) == 0:
                        for file in to_delete.file_set.all():
                            file.delete()
                        to_delete.delete()
                    else:
                        to_delete_list += [to_delete] + list(children)
        else:
            _verify_empty(directory)
            directory.delete()

        return _response_for_json(STATUS_OK)

class ShareView(AbstractView):

    def _handle_get(self, request: HttpRequest, **kwargs) -> HttpResponse:
        _verify_kwargs(kwargs, ["share_id"])

        user_id = _get_query_param(request, "user", converter=int)
        _verify_authenticated(request, user_id)

        share_id = _get_kwarg(kwargs, "share_id", converter=int)
        share = Share.objects.get(id=share_id)
        _verify_can_access(user_id, share, is_write=False)

        return _response_for_json(STATUS_OK, share=serialize_share(share, include_target=True))

    def _handle_post(self, request: HttpRequest, **kwargs) -> HttpResponse:
        _verify_kwargs(kwargs, [])

        user_id = _get_query_param(request, "user", converter=int)
        _verify_authenticated(request, user_id)

        user = StorageUser.objects.get(id=user_id)
        subject_id = _get_query_param(request, "subject", converter=int)
        subject = StorageUser.objects.get(id=subject_id)
        target_type = _get_query_param(request, "targetType", converter=_get_converter_for_enum(["file", "directory"]))
        target_id = _get_query_param(request, "targetID", converter=int)
        target = Directory.objects.get(id=target_id) if target_type == "directory" else File.objects.get(id=target_id)
        _verify_can_access(user_id, target, is_write=True)
        can_write = _get_query_param(request, "canWrite", required=False, converter=_get_converter_for_enum([""])) == ""

        if target.owner.id != user_id:
            raise _ErrorAccessDenied()
        if user_id == subject_id:
            raise _ErrorInvalidShareSubject()

        target_dict = {"target_directory" if target_type == "directory" else "target_file": target}
        share = Share.objects.create(issuer=user, subject=subject, **target_dict, can_write=can_write)

        return _response_for_json(STATUS_OK, share=serialize_share(share))

    def _handle_delete(self, request: HttpRequest, **kwargs):
        _verify_kwargs(kwargs, ["share_id"])

        user_id = _get_query_param(request, "user", converter=int)
        _verify_authenticated(request, user_id)

        share_id = _get_kwarg(kwargs, "share_id", converter=int)
        share = Share.objects.get(id=share_id)
        _verify_can_access(user_id, share, is_write=True)

        share.delete()

        return _response_for_json(STATUS_OK)

class ContactView(AbstractView):

    def _handle_get(self, request: HttpRequest, **kwargs) -> HttpResponse:
        _verify_kwargs(kwargs, ["contact_id"])

        user_id = _get_query_param(request, "user", converter=int)
        _verify_authenticated(request, user_id)

        contact_id = _get_kwarg(kwargs, "contact_id", converter=int)
        contact = StorageUser.objects.get(id=contact_id)

        return _response_for_json(STATUS_OK, contact=serialize_storage_user(contact))

    def _handle_post(self, request: HttpRequest, **kwargs) -> HttpResponse:
        _verify_kwargs(kwargs, ["contact_id"])

        user_id = _get_query_param(request, "user", converter=int)
        _verify_authenticated(request, user_id)

        user = StorageUser.objects.get(id=user_id)
        contact_id = _get_kwarg(kwargs, "contact_id", converter=int)
        if contact_id == user_id:
            raise _ErrorInvalidContact()
        contact = StorageUser.objects.get(id=contact_id)

        if contact not in user.contacts.all():
            user.contacts.add(contact)
            user.save()

        return _response_for_json(STATUS_OK, contact=serialize_storage_user(contact))

    def _handle_delete(self, request: HttpRequest, **kwargs):
        _verify_kwargs(kwargs, ["contact_id"])

        user_id = _get_query_param(request, "user", converter=int)
        _verify_authenticated(request, user_id)

        user = StorageUser.objects.get(id=user_id)
        contact_id = _get_kwarg(kwargs, "contact_id", converter=int)
        user.contacts.get(id=contact_id)

        user.contacts.remove(contact_id)
        user.save()

        return _response_for_json(STATUS_OK)

class _ErrorBadRequest(Exception):
    def __init__(self, msg: str):
        self.msg = msg

class _ErrorUnauthorized(Exception):
    pass

class _ErrorAccessDenied(Exception):
    pass

class _ErrorNotFound(Exception):
    pass

class _ErrorTransferralRejected(Exception):
    pass

class _ErrorDuplicateName(Exception):
    pass

class _ErrorCyclicDirectoryTree(Exception):
    pass

class _ErrorUnmovableDirectory(Exception):
    pass

class _ErrorDirectoryNotEmpty(Exception):
    pass

class _ErrorInvalidShareSubject(Exception):
    pass

class _ErrorInvalidContact(Exception):
    pass

class _ErrorQuotaExceeded(Exception):
    pass

def _get_kwarg(kwargs: dict[str, Any], name: str, required=True, default=None, converter: Callable[[str], Any]=lambda x: x) -> Any:
    if name in kwargs:
        try:
            return converter(kwargs[name])
        except ValueError:
            raise _ErrorBadRequest("Malformed value for \"%s\"" % name)
    if required:
        raise _ErrorBadRequest("Missing value for \"%s\"" % name)
    return default

def _verify_kwargs(kwargs: dict[str, Any], valid_names: List[str]):
    for name in kwargs:
        if name not in valid_names:
            raise _ErrorBadRequest("Cannot have value for \"%s\"" % name)

def _get_query_param(req: HttpRequest, name: str, required=True, default=None, converter: Callable[[str], Any]=lambda x: x) -> Any:
    query_dict = req.GET
    if name in query_dict:
        try:
            return converter(query_dict[name])
        except ValueError:
            raise _ErrorBadRequest("Malformed value for query parameter \"%s\"" % name)
    if required:
        raise _ErrorBadRequest("Missing required query parameter \"%s\"" % name)
    return default

def _get_converter_for_enum(accepted_values: List[str], converter: Callable[[str], Any]=lambda x: x) -> Callable[[str], Any]:
    def res(x: str):
        if x not in accepted_values:
            raise ValueError(str(x) + " is not a permitted value")
        return converter(x)
    return res

def _verify_authenticated(req: HttpRequest, user_id: int) -> (StorageUser, dict[str, Any]):

    token = req.headers.get(AUTHORIZATION_HEADER_NAME, None)
    if token is None:
        logging.info("No token was supplied for user with ID %s" % user_id)
        raise _ErrorUnauthorized()
    regex = re.compile("^Bearer ([^ ]+)$")
    if regex.match(token) is None:
        logging.info("An malformed token was supplied for user with ID %s" % user_id)
        raise _ErrorUnauthorized()
    token = regex.sub("\\1", token)

    try:
        token = jwt.decode(token, settings.SIMPLE_JWT["VERIFYING_KEY"], settings.SIMPLE_JWT["ALGORITHM"])
    except Exception:
        logging.info("An invalid token was supplied for user with ID %s" % user_id)
        raise _ErrorUnauthorized()

    for key, expected_type in [(CLAIM_USER_ID, int), (CLAIM_USER_NAME, str), (CLAIM_ISSUED_AT, int), (CLAIM_EXPIRES_AT, int), (CLAIM_TOKEN_TYPE, str)]:
        if key not in token:
            logging.info("Encountered a valid token for user with ID %s which is missing the required claim \"%s\"" % (user_id, key))
            raise _ErrorUnauthorized()
        if expected_type != type(token[key]):
            logging.info("Encountered a valid token for user with ID %s which contains an invalid value for the required claim \"%s\"" % (user_id, key))
            raise _ErrorUnauthorized()
    claimed_user_id = token[CLAIM_USER_ID]
    if claimed_user_id != user_id:
        logging.info("User with ID %s attempted to authenticate as user with ID %s" % (claimed_user_id, user_id))
        raise _ErrorUnauthorized()
    now = int(time.time())
    if now > token[CLAIM_EXPIRES_AT]:
        logging.info("User with ID %s attempted to authenticate using an expired token" % user_id)
        raise _ErrorUnauthorized()
    if now < token[CLAIM_ISSUED_AT]:
        logging.info("User with ID %s attempted to authenticate using a token issued in the future. This may be due to incorrect system time configuration" % user_id)
        raise _ErrorUnauthorized()
    if ACCESS_TOKEN_TYPE != token[CLAIM_TOKEN_TYPE]:
        logging.info("User with ID %s attempted to authenticate using a token of a type other than \"%s\"" % (user_id, ACCESS_TOKEN_TYPE))
        raise _ErrorUnauthorized()

    try:
        with transaction.atomic():
            try:
                user = StorageUser.objects.get(id=user_id)
            except ObjectDoesNotExist:
                user = StorageUser.objects.create(id=user_id, display_name=token[CLAIM_USER_NAME])
                Directory.objects.create(name="root", owner=user, parent=None)
                logging.info("Created new user with ID %s and display name \"%s\"" % (user_id, token[CLAIM_USER_NAME]))
    except DatabaseError as db_error:
        try:
            user = StorageUser.objects.get(id=user_id)
        except ObjectDoesNotExist:
            raise db_error

    return user, token

def _verify_can_access(user_id: int, obj: Union[StorageUser, File, Directory, Share], is_write: bool) -> None:
    if obj is None:
        return
    if not obj.can_access(user_id, False):
        logging.info("User %d attempted to read %s, but is lacking the permission to do so" % (user_id, obj))
        raise _ErrorNotFound()
    if is_write and not obj.can_access(user_id, True):
        logging.info("User %d attempted to write %s, but is lacking the permission to do so" % (user_id, obj))
        raise _ErrorAccessDenied()

def _verify_same_owner(*args: Union[File, Directory]) -> None:
    if len(args) <= 1:
        return
    owner = args[0].owner
    for e in args:
        if e.owner != owner:
            raise _ErrorTransferralRejected()

def _verify_unique_names(to_check: Union[str, File, Directory], directory: Directory) -> None:
    if type(to_check) == str:
        for e in [*directory.file_set.all(), *directory.directory_set.all()]:
            if e.name == to_check:
                raise _ErrorDuplicateName()
    else:
        for e in [*directory.file_set.all(), *directory.directory_set.all()]:
            if e != to_check and e.name == to_check.name:
                raise _ErrorDuplicateName()

def _verify_movable(to_check: Directory, target_dir: Directory) -> None:
    if to_check.parent is None:
        raise _ErrorUnmovableDirectory()
    while target_dir is not None:
        if to_check == target_dir:
            raise _ErrorCyclicDirectoryTree()
        target_dir = target_dir.parent

def _verify_empty(to_check: Directory):
    if len(to_check.file_set.all()) > 0 or len(to_check.directory_set.all()) > 0:
        raise _ErrorDirectoryNotEmpty()

def _verify_quota(user_id: int, upload_size: int, excluded_file_id: Union[int, None]=None) -> None:
    with connection.cursor() as c:
        if excluded_file_id is None:
            c.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) AS sum FROM \"File\" WHERE owner_id = %s", [user_id])
        else:
            c.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) AS sum FROM \"File\" WHERE owner_id = %s AND id <> %s", [user_id, excluded_file_id])
        size_response = c.fetchone()
        if size_response is None:
            raise _ErrorQuotaExceeded()
        size = size_response[0]
    if size + upload_size > LIMIT_USER_TOTAL_SIZE:
        raise _ErrorQuotaExceeded()

def _response_for_json(status: [str, int], **kwargs) -> HttpResponse:
    return _create_json_response({
        "status": status[0],
        **kwargs
    }, status=status[1])

def _response_for_binary(status: [str, int], data: bytes) -> HttpResponse:
    return _create_binary_response(data, status[1], content_type="application/octet-stream")

def _create_json_response(json_object: dict[str, Any], status: int) -> HttpResponse:
    return _create_binary_response(bytes(json.dumps(json_object), "utf-8"), status, content_type="application/json")

def _create_binary_response(data: bytes, status: int, **kwargs) -> HttpResponse:
    return HttpResponse(data, status=status, **kwargs)
