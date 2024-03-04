from rest_framework import relations


class ManyRelatedField(relations.ManyRelatedField):

    async def ato_representation(self, iterable):
        return [
            self.child_relation.to_representation(value)
            async for value in iterable
        ]
    #
    # async def to_internal_value(self, data):
    #     return await super().to_internal_value(data)
