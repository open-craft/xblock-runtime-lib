"""
XBlock Runtime Library models

With code from:

    https://github.com/openedx/edx-platform/blob/9514cb57/openedx/core/djangoapps/xmodule_django/models.py
    https://github.com/openedx/edx-platform/blob/9514cb57/lms/djangoapps/courseware/models.py
    https://github.com/openedx/edx-platform/blob/9514cb57/common/djangoapps/student/models.py
"""


import hashlib
import itertools
import logging

from django.conf import settings
from django.contrib.auth.models import User
from django.db import models
from opaque_keys.edx.django.models import BlockTypeKeyField, LearningContextKeyField, UsageKeyField
from edx_django_utils.cache import RequestCache

from .fields import UnsignedBigIntAutoField
from .markup import HTML

log = logging.getLogger("xblock_runtime_lib")


def chunks(items, chunk_size):
    """
    Yields the values from items in chunks of size chunk_size
    """
    items = list(items)
    return (items[i:i + chunk_size] for i in range(0, len(items), chunk_size))


class ChunkingManager(models.Manager):
    """
    :class:`~Manager` that adds an additional method :meth:`chunked_filter` to provide
    the ability to make select queries with specific chunk sizes.
    """

    class Meta:
        app_label = "xblock_runtime_lib"

    def chunked_filter(self, chunk_field, items, **kwargs):
        """
        Queries model_class with `chunk_field` set to chunks of size `chunk_size`,
        and all other parameters from `**kwargs`.

        This works around a limitation in sqlite3 on the number of parameters
        that can be put into a single query.

        Arguments:
            chunk_field (str): The name of the field to chunk the query on.
            items: The values for of chunk_field to select. This is chunked into ``chunk_size``
                chunks, and passed as the value for the ``chunk_field`` keyword argument to
                :meth:`~Manager.filter`. This implies that ``chunk_field`` should be an
                ``__in`` key.
            chunk_size (int): The size of chunks to pass. Defaults to 500.
        """
        chunk_size = kwargs.pop('chunk_size', 500)
        res = itertools.chain.from_iterable(
            self.filter(**dict([(chunk_field, chunk)] + list(kwargs.items())))
            for chunk in chunks(items, chunk_size)
        )
        return res


class NoneToEmptyManager(models.Manager):
    """
    A :class:`django.db.models.Manager` that has a :class:`NoneToEmptyQuerySet`
    as its `QuerySet`, initialized with a set of specified `field_names`.
    """
    def get_queryset(self):
        """
        Returns the result of NoneToEmptyQuerySet instead of a regular QuerySet.
        """
        return NoneToEmptyQuerySet(self.model, using=self._db)


class NoneToEmptyQuerySet(models.query.QuerySet):
    """
    A :class:`django.db.query.QuerySet` that replaces `None` values passed to `filter` and `exclude`
    with the corresponding `Empty` value for all fields with an `Empty` attribute.

    This is to work around Django automatically converting `exact` queries for `None` into
    `isnull` queries before the field has a chance to convert them to queries for it's own
    empty value.
    """
    def _filter_or_exclude(self, *args, **kwargs):
        for field_object in self.model._meta.get_fields():
            direct = not field_object.auto_created or field_object.concrete
            if direct and hasattr(field_object, 'Empty'):
                for suffix in ('', '_exact'):
                    key = f'{field_object.name}{suffix}'
                    if key in kwargs and kwargs[key] is None:
                        kwargs[key] = field_object.Empty

        return super()._filter_or_exclude(*args, **kwargs)


class StudentModule(models.Model):
    """
    Keeps student state for a particular XBlock usage and particular student.

    """
    objects = ChunkingManager()

    id = UnsignedBigIntAutoField(primary_key=True)

    ## The XBlock/XModule type (e.g. "problem")
    module_type = models.CharField(max_length=32, db_index=True)

    # Key used to share state. This is the XBlock usage_id
    module_state_key = UsageKeyField(max_length=255, db_column='module_id')
    student = models.ForeignKey(User, db_index=True, db_constraint=False, on_delete=models.CASCADE)

    # The learning context of the usage_key (usually a course ID, but may be a library or something else)
    course_id = LearningContextKeyField(max_length=255, db_index=True)

    class Meta:
        app_label = "xblock_runtime_lib"
        unique_together = (('student', 'module_state_key', 'course_id'),)
        indexes = [
            models.Index(fields=['module_state_key', 'grade', 'student'], name="courseware_stats")
        ]

    # Internal state of the object
    state = models.TextField(null=True, blank=True)

    # Grade, and are we done?
    grade = models.FloatField(null=True, blank=True, db_index=True)
    max_grade = models.FloatField(null=True, blank=True)
    DONE_TYPES = (
        ('na', 'NOT_APPLICABLE'),
        ('f', 'FINISHED'),
        ('i', 'INCOMPLETE'),
    )
    done = models.CharField(max_length=8, choices=DONE_TYPES, default='na')

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    modified = models.DateTimeField(auto_now=True, db_index=True)

    @classmethod
    def all_submitted_problems_read_only(cls, course_id):
        """
        Return all model instances that correspond to problems that have been
        submitted for a given course. So module_type='problem' and a non-null
        grade. Use a read replica if one exists for this environment.
        """
        queryset = cls.objects.filter(
            course_id=course_id,
            module_type='problem',
            grade__isnull=False
        )
        if "read_replica" in settings.DATABASES:
            return queryset.using("read_replica")
        else:
            return queryset

    def __repr__(self):
        return 'StudentModule<{!r}>'.format(
            {
                'course_id': self.course_id,
                'module_type': self.module_type,
                # We use the student_id instead of username to avoid a database hop.
                # This can actually matter in cases where we're logging many of
                # these (e.g. on a broken progress page).
                'student_id': self.student_id,
                'module_state_key': self.module_state_key,
                'state': str(self.state)[:20],
            })

    def __str__(self):
        return str(repr(self))

    @classmethod
    def get_state_by_params(cls, course_id, module_state_keys, student_id=None):
        """
        Return all model instances that correspond to a course and module keys.

        Student ID is optional keyword argument, if provided it narrows down the instances.
        """
        module_states = cls.objects.filter(course_id=course_id, module_state_key__in=module_state_keys)
        if student_id:
            module_states = module_states.filter(student_id=student_id)
        return module_states

    @classmethod
    def save_state(cls, student, course_id, module_state_key, defaults):
        if not student.is_authenticated:
            return
        else:
            cls.objects.update_or_create(
                student=student,
                course_id=course_id,
                module_state_key=module_state_key,
                defaults=defaults,
            )


class XBlockFieldBase(models.Model):
    """
    Base class for all XBlock field storage.

    .. no_pii:
    """
    objects = ChunkingManager()

    class Meta:
        app_label = "xblock_runtime_lib"
        abstract = True

    # The name of the field
    field_name = models.CharField(max_length=64, db_index=True)

    # The value of the field. Defaults to None dumped as json
    value = models.TextField(default='null')

    created = models.DateTimeField(auto_now_add=True, db_index=True)
    modified = models.DateTimeField(auto_now=True, db_index=True)

    def __str__(self):
        keys = [field.name for field in self._meta.get_fields() if field.name not in ('created', 'modified')]
        return HTML('{}<{!r}').format(
            HTML(self.__class__.__name__),
            {key: HTML(getattr(self, key)) for key in keys}
        )


class XModuleUserStateSummaryField(XBlockFieldBase):
    """
    Stores data set in the Scope.user_state_summary scope by an xmodule field
    """

    class Meta:
        app_label = "xblock_runtime_lib"
        unique_together = (('usage_id', 'field_name'),)

    # The definition id for the module
    usage_id = UsageKeyField(max_length=255, db_index=True)


class XModuleStudentPrefsField(XBlockFieldBase):
    """
    Stores data set in the Scope.preferences scope by an xmodule field
    """

    class Meta:
        app_label = "xblock_runtime_lib"
        unique_together = (('student', 'module_type', 'field_name'),)

    # The type of the module for these preferences
    module_type = BlockTypeKeyField(max_length=64, db_index=True)

    student = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)


class XModuleStudentInfoField(XBlockFieldBase):
    """
    Stores data set in the Scope.preferences scope by an xmodule field
    """

    class Meta:
        app_label = "xblock_runtime_lib"
        unique_together = (('student', 'field_name'),)

    student = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)


class AnonymousUserId(models.Model):
    """
    This table contains user, course_Id and anonymous_user_id

    Purpose of this table is to provide user by anonymous_user_id.

    We generate anonymous_user_id using md5 algorithm,
    and use result in hex form, so its length is equal to 32 bytes.

    .. no_pii: We store anonymous_user_ids here, but do not consider them PII under OEP-30.
    """

    objects = NoneToEmptyManager()

    user = models.ForeignKey(User, db_index=True, on_delete=models.CASCADE)
    anonymous_user_id = models.CharField(unique=True, max_length=32)
    course_id = LearningContextKeyField(db_index=True, max_length=255, blank=True)


def anonymous_id_for_user(user, course_id, save='DEPRECATED'):
    """
    Inputs:
        user: User model
        course_id: string or None
        save: Deprecated and ignored: ID is always saved in an AnonymousUserId object

    Return a unique id for a (user, course_id) pair, suitable for inserting
    into e.g. personalized survey links.

    If user is an `AnonymousUser`, returns `None`
    else If this user/course_id pair already has an anonymous id in AnonymousUserId object, return that
    else: create new anonymous_id, save it in AnonymousUserId, and return anonymous id
    """

    # This part is for ability to get xblock instance in xblock_noauth handlers, where user is unauthenticated.
    assert user

    if save != 'DEPRECATED':
        warnings.warn(
            "anonymous_id_for_user no longer accepts save param and now "
            "always saves the ID in the database",
            DeprecationWarning
        )

    if user.is_anonymous:
        return None

    cached_id = getattr(user, '_anonymous_id', {}).get(course_id)
    if cached_id is not None:
        return cached_id

    # Check if an anonymous id already exists for this user and
    # course_id combination. Prefer the one with the highest record ID
    # (see below.)
    anonymous_user_ids = AnonymousUserId.objects.filter(user=user).filter(course_id=course_id).order_by('-id')
    if anonymous_user_ids:
        # If there are multiple anonymous_user_ids per user, course_id pair
        # select the row which was created most recently.
        # There might be more than one if the Django SECRET_KEY had
        # previously been rotated at a time before this function was
        # changed to always save the generated IDs to the DB. In that
        # case, just pick the one with the highest record ID, which is
        # probably the most recently created one.
        anonymous_user_id = anonymous_user_ids[0].anonymous_user_id
    else:
        # Uses SECRET_KEY as a cryptographic pepper. This
        # deterministic ID generation means that concurrent identical
        # calls to this function return the same value -- no need for
        # locking. (There may be a low level of integrity errors on
        # creation as a result of concurrent duplicate row inserts.)
        #
        # Consequences for this function of SECRET_KEY exposure: Data
        # researchers and other third parties receiving these
        # anonymous user IDs would be able to identify users across
        # courses, and predict the anonymous user IDs of all users
        # (but not necessarily identify their accounts.)
        #
        # Rotation process of SECRET_KEY with respect to this
        # function: Rotate at will, since the hashes are stored and
        # will not change.
        # include the secret key as a salt, and to make the ids unique across different LMS installs.
        hasher = hashlib.shake_128()
        hasher.update(settings.SECRET_KEY.encode('utf8'))
        hasher.update(str(user.id).encode('utf8'))
        if course_id:
            hasher.update(str(course_id).encode('utf-8'))
        anonymous_user_id = hasher.hexdigest(16)

        try:
            AnonymousUserId.objects.create(
                user=user,
                course_id=course_id,
                anonymous_user_id=anonymous_user_id,
            )
        except IntegrityError:
            # Another thread has already created this entry, so
            # continue
            pass

    # cache the anonymous_id in the user object
    if not hasattr(user, '_anonymous_id'):
        user._anonymous_id = {}
    user._anonymous_id[course_id] = anonymous_user_id

    return anonymous_user_id


def user_by_anonymous_id(uid):
    """
    Return user by anonymous_user_id using AnonymousUserId lookup table.

    Do not raise `django.ObjectDoesNotExist` exception,
    if there is no user for anonymous_student_id,
    because this function can be used without django access.
    """

    if uid is None:
        return None

    request_cache = RequestCache('user_by_anonymous_id')
    cache_response = request_cache.get_cached_response(uid)
    if cache_response.is_found:
        return cache_response.value

    try:
        user = User.objects.get(anonymoususerid__anonymous_user_id=uid)
        request_cache.set(uid, user)
        return user
    except ObjectDoesNotExist:
        request_cache.set(uid, None)
        return None


def get_user_by_username_or_email(username_or_email):
    """
    Return a User object by looking up a user against username_or_email.

    Raises:
        User.DoesNotExist if no user object can be found, the user was
        retired, or the user is in the process of being retired.

        MultipleObjectsReturned if one user has same email as username of
        second user

        MultipleObjectsReturned if more than one user has same email or
        username
    """
    username_or_email = strip_if_string(username_or_email)
    # there should be one user with either username or email equal to username_or_email
    user = User.objects.get(Q(email=username_or_email) | Q(username=username_or_email))
    return user


def strip_if_string(value):
    if isinstance(value, str):
        return value.strip()
    return value
