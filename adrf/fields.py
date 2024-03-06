import asyncio

from rest_framework.fields import SerializerMethodField as DRFSerializerMethodField


class SerializerMethodField(DRFSerializerMethodField):

    async def ato_representation(self, value):
        method = getattr(self.parent, self.method_name)
        return await method(value)
