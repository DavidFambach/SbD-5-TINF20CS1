import logging

from . import messaging
from django.core.exceptions import ObjectDoesNotExist
from django.db import models
from secrets import SystemRandom
from typing import Any

RNG = SystemRandom()

class StorageUser(models.Model):
    class Meta:
        db_table = "StorageUser"
        constraints = []
    objects = models.Manager()

    id = models.BigAutoField(primary_key=True)
    display_name = models.CharField(max_length=256)
    contacts = models.ManyToManyField("StorageUser")

    def can_access(self, user_id: int, is_write: bool) -> bool:
        return not is_write and user_id == self.id

class Directory(models.Model):
    class Meta:
        db_table = "Directory"
        constraints = []
    objects = models.Manager()

    id = models.BigAutoField(primary_key=True, blank=True)
    name = models.CharField(max_length=256)
    owner = models.ForeignKey("StorageUser", on_delete=models.CASCADE)
    parent = models.ForeignKey("Directory", on_delete=models.RESTRICT, blank=True, null=True)

    file_set: Any
    directory_set: Any

    def save(self, *args, **kwargs):
        while not self.id:
            rnd_id = _create_random_id()
            if not Directory.objects.filter(pk=rnd_id).exists():
                self.id = rnd_id
        super(Directory, self).save()

    def can_access(self, user_id: int, is_write: bool) -> bool:
        # The owner always has permissions
        if self.owner.id == user_id:
            return True
        # Check diectory shares
        for share in self.share_set.filter(subject=user_id).all():
            if share.can_write:
                return True
            if not is_write:
                return True
        # Check parent directory shares
        if self.parent is not None:
            return self.parent.can_access(user_id, is_write)
        return False

class File(models.Model):
    class Meta:
        db_table = "File"
        constraints = []
    objects = models.Manager()

    id = models.BigAutoField(primary_key=True, blank=True)
    name = models.CharField(max_length=256)
    owner = models.ForeignKey("StorageUser", on_delete=models.CASCADE)
    parent_directory = models.ForeignKey("Directory", on_delete=models.RESTRICT)
    data = models.BinaryField()

    def save(self, *args, **kwargs):
        while not self.id:
            rnd_id = _create_random_id()
            if not File.objects.filter(pk=rnd_id).exists():
                self.id = rnd_id
        super(File, self).save()

    def can_access(self, user_id: int, is_write: bool) -> bool:
        # The owner always has permissions
        if self.owner.id == user_id:
            return True
        # Check file shares
        for share in self.share_set.filter(subject=user_id).all():
            if share.can_write:
                return True
            if not is_write:
                return True
        # Check parent directory shares
        return self.parent_directory.can_access(user_id, is_write)

class Share(models.Model):
    class Meta:
        db_table = "Share"
        constraints = []
    objects = models.Manager()

    id = models.BigAutoField(primary_key=True, blank=True)
    issuer = models.ForeignKey("StorageUser", on_delete=models.CASCADE, related_name="shares_issued")
    subject = models.ForeignKey("StorageUser", on_delete=models.CASCADE, related_name="shares_received")
    target_file = models.ForeignKey("File", on_delete=models.CASCADE, blank=True, null=True)
    target_directory = models.ForeignKey("Directory", on_delete=models.CASCADE, blank=True, null=True)
    can_write = models.BooleanField()

    def save(self, *args, **kwargs):
        while not self.id:
            rnd_id = _create_random_id()
            if not Share.objects.filter(pk=rnd_id).exists():
                self.id = rnd_id
        super(Share, self).save()

    def can_access(self, user_id: int, is_write: bool) -> bool:
        if self.issuer.id == user_id:
            return True
        if not is_write and self.subject.id == user_id:
            return True
        return False

def _delete_user(user_id: int) -> None:
    try:
        user = StorageUser.objects.get(id=user_id)
    except ObjectDoesNotExist:
        return
    user.delete()
    logging.info("Successfully deleted user with ID %s" % user_id)
messaging.DELETE_USER_CALLBACK = _delete_user

def _create_random_id():
    return RNG.randint(1, 9007199254740992)
