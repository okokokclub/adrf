from rest_framework import relations


class ManyRelatedField(relations.ManyRelatedField):

    async def ato_representation(self, iterable):
        return [
            self.child_relation.to_representation(value)
            async for value in iterable
        ]


class PrimaryKeyRelatedField(relations.PrimaryKeyRelatedField):

    async def ato_representation(self, value):
        if self.pk_field is not None:
            return await self.pk_field.ato_representation(value.pk)
        return value.pk
