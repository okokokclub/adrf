"""
Basic building blocks for generic class based views.

We don't bind behaviour to http method handlers yet,
which allows mixin classes to be composed in interesting ways.
"""
from asgiref.sync import sync_to_async
from rest_framework import status
from rest_framework import mixins
from rest_framework.response import Response
from rest_framework.settings import api_settings


class CreateModelMixin(mixins.CreateModelMixin):
    """
    Create a model instance.
    """

    def get_success_headers(self, data):
        try:
            return {'Location': str(data[api_settings.URL_FIELD_NAME])}
        except (TypeError, KeyError):
            return {}

    async def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        await sync_to_async(serializer.is_valid)(raise_exception=True)
        await self.aperform_create(serializer)
        data = await serializer.adata
        headers = self.get_success_headers(data)
        return Response(data, status=status.HTTP_201_CREATED, headers=headers)

    async def aperform_create(self, serializer):
        await serializer.asave()


class ListModelMixin(mixins.ListModelMixin):
    """
    List a queryset.
    """

    async def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        page = await self.apaginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            data = await serializer.adata
            return self.get_paginated_response(data)

        serializer = self.get_serializer(queryset, many=True)
        data = await serializer.adata
        return Response(data)


class RetrieveModelMixin(mixins.RetrieveModelMixin):
    """
    Retrieve a model instance.
    """

    async def retrieve(self, request, *args, **kwargs):
        instance = await self.aget_object()
        serializer = self.get_serializer(instance)
        data = await serializer.adata
        return Response(data)


class UpdateModelMixin(mixins.UpdateModelMixin):
    """
    Update a model instance.
    """

    async def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        instance = await self.aget_object()
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        await self.aperform_update(serializer)

        if getattr(instance, '_prefetched_objects_cache', None):
            # If 'prefetch_related' has been applied to a queryset, we need to
            # forcibly invalidate the prefetch cache on the instance.
            instance._prefetched_objects_cache = {}

        data = await serializer.adata
        return Response(data)

    async def aperform_update(self, serializer):
        await serializer.asave()

    async def partial_update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return await self.update(request, *args, **kwargs)


class DestroyModelMixin(mixins.DestroyModelMixin):
    """
    Destroy a model instance.
    """

    async def destroy(self, request, *args, **kwargs):
        instance = await self.aget_object()
        await self.aperform_destroy(instance)
        return Response(status=status.HTTP_204_NO_CONTENT)

    async def aperform_destroy(self, instance):
        await instance.adelete()
