from typing import Iterable

from django.db import models
from rest_framework import relations


class ManyRelatedField(relations.ManyRelatedField):

    async def ato_representation(self, iterable: Iterable | models.QuerySet):
        objects = iterable
        if isinstance(iterable, models.QuerySet):
            objects = [obj async for obj in iterable]
        return [
            self.child_relation.to_representation(obj)
            for obj in objects
        ]


class PrimaryKeyRelatedField(relations.PrimaryKeyRelatedField):

    async def ato_representation(self, value):
        if self.pk_field is not None:
            return await self.pk_field.ato_representation(value.pk)
        return value.pk
