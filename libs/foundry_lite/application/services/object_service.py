from __future__ import annotations

from foundry_lite.application.services.object_store.indexing import ObjectIndexingMixin
from foundry_lite.application.services.object_store.links import ObjectLinksMixin
from foundry_lite.application.services.object_store.query import ObjectQueryMixin
from foundry_lite.application.services.object_store.records import ObjectRecordsMixin
from foundry_lite.application.services.object_store.sets import ObjectSetsMixin


class ObjectServiceMixin(ObjectQueryMixin, ObjectSetsMixin, ObjectIndexingMixin, ObjectLinksMixin, ObjectRecordsMixin):
    pass
