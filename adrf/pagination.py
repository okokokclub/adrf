"""
Pagination serializers determine the structure of the output that should
be used for paginated responses.
"""
from base64 import b64decode, b64encode
from collections import OrderedDict
from urllib import parse

from asgiref.sync import sync_to_async
from django.template import loader
from django.utils.encoding import force_str
from django.utils.translation import gettext_lazy as _
from rest_framework.compat import coreapi, coreschema
from rest_framework.exceptions import NotFound
from rest_framework.pagination import BasePagination as DRFBasePagination, _reverse_ordering, _positive_int, Cursor
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.utils.urls import replace_query_param


class BasePagination(DRFBasePagination):
    display_page_controls = False

    async def apaginate_queryset(self, queryset, request, view=None):  # pragma: no cover
        raise NotImplementedError('apaginate_queryset() must be implemented.')


class CursorPagination(BasePagination):
    """
    The cursor pagination implementation is necessarily complex.
    For an overview of the position/offset style we use, see this post:
    https://cra.mr/2011/03/08/building-cursors-for-the-disqus-api
    """
    cursor_query_param = 'cursor'
    cursor_query_description = _('The pagination cursor value.')
    page_size = api_settings.PAGE_SIZE
    invalid_cursor_message = _('Invalid cursor')
    ordering = '-created'
    template = 'rest_framework/pagination/previous_and_next.html'

    # Client can control the page size using this query parameter.
    # Default is 'None'. Set to eg 'page_size' to enable usage.
    page_size_query_param = None
    page_size_query_description = _('Number of results to return per page.')

    # Set to an integer to limit the maximum page size the client may request.
    # Only relevant if 'page_size_query_param' has also been set.
    max_page_size = None

    # The offset in the cursor is used in situations where we have a
    # nearly-unique index. (Eg millisecond precision creation timestamps)
    # We guard against malicious users attempting to cause expensive database
    # queries, by having a hard cap on the maximum possible size of the offset.
    offset_cutoff = 1000

    def paginate_queryset(self, queryset, request, view=None):
        self.page_size = self.get_page_size(request)
        if not self.page_size:
            return None

        self.base_url = request.build_absolute_uri()
        self.ordering = self.get_ordering(request, queryset, view)

        self.cursor = self.decode_cursor(request)
        if self.cursor is None:
            (offset, reverse, current_position) = (0, False, None)
        else:
            (offset, reverse, current_position) = self.cursor

        # Cursor pagination always enforces an ordering.
        if reverse:
            queryset = queryset.order_by(*_reverse_ordering(self.ordering))
        else:
            queryset = queryset.order_by(*self.ordering)

        # If we have a cursor with a fixed position then filter by that.
        if current_position is not None:
            order = self.ordering[0]
            is_reversed = order.startswith('-')
            order_attr = order.lstrip('-')

            # Test for: (cursor reversed) XOR (queryset reversed)
            if self.cursor.reverse != is_reversed:
                kwargs = {order_attr + '__lt': current_position}
            else:
                kwargs = {order_attr + '__gt': current_position}

            queryset = queryset.filter(**kwargs)

        # If we have an offset cursor then offset the entire page by that amount.
        # We also always fetch an extra item in order to determine if there is a
        # page following on from this one.
        results = list(queryset[offset:offset + self.page_size + 1])
        self.page = list(results[:self.page_size])

        # Determine the position of the final item following the page.
        if len(results) > len(self.page):
            has_following_position = True
            following_position = self._get_position_from_instance(results[-1], self.ordering)
        else:
            has_following_position = False
            following_position = None

        if reverse:
            # If we have a reverse queryset, then the query ordering was in reverse
            # so we need to reverse the items again before returning them to the user.
            self.page = list(reversed(self.page))

            # Determine next and previous positions for reverse cursors.
            self.has_next = (current_position is not None) or (offset > 0)
            self.has_previous = has_following_position
            if self.has_next:
                self.next_position = current_position
            if self.has_previous:
                self.previous_position = following_position
        else:
            # Determine next and previous positions for forward cursors.
            self.has_next = has_following_position
            self.has_previous = (current_position is not None) or (offset > 0)
            if self.has_next:
                self.next_position = following_position
            if self.has_previous:
                self.previous_position = current_position

        # Display page controls in the browsable API if there is more
        # than one page.
        if (self.has_previous or self.has_next) and self.template is not None:
            self.display_page_controls = True

        return self.page

    async def apaginate_queryset(self, queryset, request, view=None):
        return await sync_to_async(self.paginate_queryset)(queryset, request, view)

    def get_page_size(self, request):
        if self.page_size_query_param:
            try:
                return _positive_int(
                    request.query_params[self.page_size_query_param],
                    strict=True,
                    cutoff=self.max_page_size
                )
            except (KeyError, ValueError):
                pass

        return self.page_size

    def get_next_link(self):
        if not self.has_next:
            return None

        if self.page and self.cursor and self.cursor.reverse and self.cursor.offset != 0:
            # If we're reversing direction and we have an offset cursor
            # then we cannot use the first position we find as a marker.
            compare = self._get_position_from_instance(self.page[-1], self.ordering)
        else:
            compare = self.next_position
        offset = 0

        has_item_with_unique_position = False
        for item in reversed(self.page):
            position = self._get_position_from_instance(item, self.ordering)
            if position != compare:
                # The item in this position and the item following it
                # have different positions. We can use this position as
                # our marker.
                has_item_with_unique_position = True
                break

            # The item in this position has the same position as the item
            # following it, we can't use it as a marker position, so increment
            # the offset and keep seeking to the previous item.
            compare = position
            offset += 1

        if self.page and not has_item_with_unique_position:
            # There were no unique positions in the page.
            if not self.has_previous:
                # We are on the first page.
                # Our cursor will have an offset equal to the page size,
                # but no position to filter against yet.
                offset = self.page_size
                position = None
            elif self.cursor.reverse:
                # The change in direction will introduce a paging artifact,
                # where we end up skipping forward a few extra items.
                offset = 0
                position = self.previous_position
            else:
                # Use the position from the existing cursor and increment
                # it's offset by the page size.
                offset = self.cursor.offset + self.page_size
                position = self.previous_position

        if not self.page:
            position = self.next_position

        cursor = Cursor(offset=offset, reverse=False, position=position)
        return self.encode_cursor(cursor)

    def get_previous_link(self):
        if not self.has_previous:
            return None

        if self.page and self.cursor and not self.cursor.reverse and self.cursor.offset != 0:
            # If we're reversing direction and we have an offset cursor
            # then we cannot use the first position we find as a marker.
            compare = self._get_position_from_instance(self.page[0], self.ordering)
        else:
            compare = self.previous_position
        offset = 0

        has_item_with_unique_position = False
        for item in self.page:
            position = self._get_position_from_instance(item, self.ordering)
            if position != compare:
                # The item in this position and the item following it
                # have different positions. We can use this position as
                # our marker.
                has_item_with_unique_position = True
                break

            # The item in this position has the same position as the item
            # following it, we can't use it as a marker position, so increment
            # the offset and keep seeking to the previous item.
            compare = position
            offset += 1

        if self.page and not has_item_with_unique_position:
            # There were no unique positions in the page.
            if not self.has_next:
                # We are on the final page.
                # Our cursor will have an offset equal to the page size,
                # but no position to filter against yet.
                offset = self.page_size
                position = None
            elif self.cursor.reverse:
                # Use the position from the existing cursor and increment
                # it's offset by the page size.
                offset = self.cursor.offset + self.page_size
                position = self.next_position
            else:
                # The change in direction will introduce a paging artifact,
                # where we end up skipping back a few extra items.
                offset = 0
                position = self.next_position

        if not self.page:
            position = self.previous_position

        cursor = Cursor(offset=offset, reverse=True, position=position)
        return self.encode_cursor(cursor)

    def get_ordering(self, request, queryset, view):
        """
        Return a tuple of strings, that may be used in an `order_by` method.
        """
        ordering_filters = [
            filter_cls for filter_cls in getattr(view, 'filter_backends', [])
            if hasattr(filter_cls, 'get_ordering')
        ]

        if ordering_filters:
            # If a filter exists on the view that implements `get_ordering`
            # then we defer to that filter to determine the ordering.
            filter_cls = ordering_filters[0]
            filter_instance = filter_cls()
            ordering = filter_instance.get_ordering(request, queryset, view)
            assert ordering is not None, (
                'Using cursor pagination, but filter class {filter_cls} '
                'returned a `None` ordering.'.format(
                    filter_cls=filter_cls.__name__
                )
            )
        else:
            # The default case is to check for an `ordering` attribute
            # on this pagination instance.
            ordering = self.ordering
            assert ordering is not None, (
                'Using cursor pagination, but no ordering attribute was declared '
                'on the pagination class.'
            )
            assert '__' not in ordering, (
                'Cursor pagination does not support double underscore lookups '
                'for orderings. Orderings should be an unchanging, unique or '
                'nearly-unique field on the model, such as "-created" or "pk".'
            )

        assert isinstance(ordering, (str, list, tuple)), (
            'Invalid ordering. Expected string or tuple, but got {type}'.format(
                type=type(ordering).__name__
            )
        )

        if isinstance(ordering, str):
            return (ordering,)
        return tuple(ordering)

    def decode_cursor(self, request):
        """
        Given a request with a cursor, return a `Cursor` instance.
        """
        # Determine if we have a cursor, and if so then decode it.
        encoded = request.query_params.get(self.cursor_query_param)
        if encoded is None:
            return None

        try:
            querystring = b64decode(encoded.encode('ascii')).decode('ascii')
            tokens = parse.parse_qs(querystring, keep_blank_values=True)

            offset = tokens.get('o', ['0'])[0]
            offset = _positive_int(offset, cutoff=self.offset_cutoff)

            reverse = tokens.get('r', ['0'])[0]
            reverse = bool(int(reverse))

            position = tokens.get('p', [None])[0]
        except (TypeError, ValueError):
            raise NotFound(self.invalid_cursor_message)

        return Cursor(offset=offset, reverse=reverse, position=position)

    def encode_cursor(self, cursor):
        """
        Given a Cursor instance, return an url with encoded cursor.
        """
        tokens = {}
        if cursor.offset != 0:
            tokens['o'] = str(cursor.offset)
        if cursor.reverse:
            tokens['r'] = '1'
        if cursor.position is not None:
            tokens['p'] = cursor.position

        querystring = parse.urlencode(tokens, doseq=True)
        encoded = b64encode(querystring.encode('ascii')).decode('ascii')
        return replace_query_param(self.base_url, self.cursor_query_param, encoded)

    def _get_position_from_instance(self, instance, ordering):
        field_name = ordering[0].lstrip('-')
        if isinstance(instance, dict):
            attr = instance[field_name]
        else:
            attr = getattr(instance, field_name)
        return str(attr)

    def get_paginated_response(self, data):
        return Response(OrderedDict([
            ('next', self.get_next_link()),
            ('previous', self.get_previous_link()),
            ('results', data)
        ]))

    def get_paginated_response_schema(self, schema):
        return {
            'type': 'object',
            'properties': {
                'next': {
                    'type': 'string',
                    'nullable': True,
                },
                'previous': {
                    'type': 'string',
                    'nullable': True,
                },
                'results': schema,
            },
        }

    def get_html_context(self):
        return {
            'previous_url': self.get_previous_link(),
            'next_url': self.get_next_link()
        }

    def to_html(self):
        template = loader.get_template(self.template)
        context = self.get_html_context()
        return template.render(context)

    def get_schema_fields(self, view):
        assert coreapi is not None, 'coreapi must be installed to use `get_schema_fields()`'
        assert coreschema is not None, 'coreschema must be installed to use `get_schema_fields()`'
        fields = [
            coreapi.Field(
                name=self.cursor_query_param,
                required=False,
                location='query',
                schema=coreschema.String(
                    title='Cursor',
                    description=force_str(self.cursor_query_description)
                )
            )
        ]
        if self.page_size_query_param is not None:
            fields.append(
                coreapi.Field(
                    name=self.page_size_query_param,
                    required=False,
                    location='query',
                    schema=coreschema.Integer(
                        title='Page size',
                        description=force_str(self.page_size_query_description)
                    )
                )
            )
        return fields

    def get_schema_operation_parameters(self, view):
        parameters = [
            {
                'name': self.cursor_query_param,
                'required': False,
                'in': 'query',
                'description': force_str(self.cursor_query_description),
                'schema': {
                    'type': 'string',
                },
            }
        ]
        if self.page_size_query_param is not None:
            parameters.append(
                {
                    'name': self.page_size_query_param,
                    'required': False,
                    'in': 'query',
                    'description': force_str(self.page_size_query_description),
                    'schema': {
                        'type': 'integer',
                    },
                }
            )
        return parameters
