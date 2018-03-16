"""Filtering logic for Multitest, Suites and testcase methods (of Suites)"""
import argparse
import collections
import fnmatch

from testplan.testing import tagging
from testplan.testing.multitest.suite import get_testsuite_name


class BaseFilter(object):
    """
        Base class for filters, supports bitwise
        operators for composing multiple filters.

        e.g. (FilterA(...) & FilterB(...)) | ~FilterC(...)
    """

    def filter(self, instance, testsuite, testcase):
        raise NotImplementedError

    def __or__(self, other):
        return Or(self, other)

    def __and__(self, other):
        return And(self, other)

    def __invert__(self):
        return Not(self)


class Filter(BaseFilter):
    """
        Noop filter class, users can inherit from
        this to implement their own filters.

        Returns True by default for all filtering operations.
    """
    category = 'common'

    def filter_instance(self, instance):
        return True

    def filter_testsuite(self, testsuite):
        return True

    def filter_testcase(self, testcase):
        return True

    def filter(self, instance, testsuite, testcase):
        if instance.enable_deep_filtering:
            return all([
                self.filter_instance(instance),
                self.filter_testsuite(testsuite),
                self.filter_testcase(testcase)
            ])
        return self.filter_instance(instance)


def flatten_filters(metafilter_kls, filters):
    """
        This is used for flattening nested filters of same type

        So when we have something like:

            Or(filter-1, filter-2) | Or(filter-3, filter-4)

        We end up with:

            Or(filter-1, filter-2, filter-3, filter-4)

        Instead of:

            Or(Or(filter-1, filter-2), Or(filter-3, filter-4))
    """
    result = []
    for f in filters:
        if isinstance(f, metafilter_kls):
            result.extend(flatten_filters(metafilter_kls, f.filters))
        else:
            result.append(f)
    return result


class MetaFilter(BaseFilter):
    """Higher level filter that allow composition of other filters."""

    operator_str = None

    def __init__(self, *filters):
        self.filters = flatten_filters(self.__class__, filters)
        self._composed_filter = None

    def __repr__(self):
        return '{}({})'.format(
            self.__class__.__name__,
            ', '.join([repr(f) for f in self.filters]))

    def __str__(self):
        delimiter = ' {} '.format(self.operator_str)
        return '({})'.format(
            delimiter.join([str(filter_obj) for filter_obj in self.filters]))

    def __eq__(self, other):
        return isinstance(other, self.__class__)\
               and other.filters == self.filters

    def compose(self, filters):
        raise NotImplementedError

    @property
    def composed_filter(self):
        if self._composed_filter is None:
            self._composed_filter = self.compose(self.filters)
        return self._composed_filter

    def filter(self, instance, testsuite, testcase):
        return self.composed_filter(instance, testsuite, testcase)


class Or(MetaFilter):
    """Meta filter that returns True if ANY of the child filters return True"""

    operator_str = '|'

    def compose(self, filters):
        def composed_filter(instance, testsuite, testcase):
            for filter_obj in filters:
                if filter_obj.filter(instance, testsuite, testcase):
                    return True
            return False
        return composed_filter


class And(MetaFilter):
    """Meta filter that returns True if ALL of the child filters return True"""

    operator_str = '&'

    def compose(self, filters):
        def composed_filter(instance, testsuite, testcase):
            for filter_obj in filters:
                if not filter_obj.filter(instance, testsuite, testcase):
                    return False
            return True
        return composed_filter


class Not(BaseFilter):
    """Meta filter that returns the inverse of the original filter result."""

    def __init__(self, filter_obj):
        self.filter_obj = filter_obj

    def __repr__(self):
        return '{}({})'.format(self.__class__.__name__, self.filter_obj)

    def __invert__(self):
        """Double negative returns original filter."""
        return self.filter_obj

    def __eq__(self, other):
        return isinstance(other, Not) and other.filter_obj == self.filter_obj

    def filter(self, instance, testsuite, testcase):
        return not self.filter_obj.filter(instance, testsuite, testcase)


class BaseTagFilter(Filter):
    """Base filter class for tag based filtering."""

    category = 'tag'

    def __init__(self, tags):
        self.tags_orig = tags
        self.tags = tagging.validate_tag_value(tags)

    def __repr__(self):
        return '{}(tags="{}")'.format(self.__class__.__name__, self.tags_orig)

    def get_match_func(self):
        raise NotImplementedError

    def _check_tags(self, obj, tag_getter):
        return self.get_match_func()(
            tag_arg_dict=self.tags,
            target_tag_dict=tag_getter(obj)
        )

    def filter_instance(self, instance):
        return self._check_tags(
            obj=instance, tag_getter=tagging.get_test_tags)

    def filter_testsuite(self, testsuite):
        return self._check_tags(
            obj=testsuite, tag_getter=tagging.get_suite_tags)

    def filter_testcase(self, testcase):
        return self._check_tags(
            obj=testcase, tag_getter=tagging.get_testcase_tags)


class Tags(BaseTagFilter):
    """Tag filter that returns True if ANY of the given tags match."""

    def get_match_func(self):
        return tagging.check_any_matching_tags


class TagsAll(BaseTagFilter):
    """Tag filter that returns True if ALL of the given tags match."""

    def get_match_func(self):
        return tagging.check_all_matching_tags


class Pattern(Filter):
    """
        Base class for name based, glob style filtering.

        https://docs.python.org/3.4/library/fnmatch.html

        Examples:

            <Multitest name>:<suite name>:<testcase name>
            <Multitest name>:*:<testcase name>
            *:<suite name>:*
    """

    MAX_LEVEL = 3
    DELIMITER = ':'
    ALL_MATCH = '*'

    category = 'pattern'

    def __init__(self, pattern):
        self.pattern = pattern
        patterns = self.parse_pattern(pattern)
        self.test_pattern, self.suite_pattern, self.testcase_pattern = patterns

    def __repr__(self):
        return '{}(pattern="{}")'.format(self.__class__.__name__, self.pattern)

    def parse_pattern(self, pattern):
        patterns = pattern.split(self.DELIMITER)

        if len(patterns) > self.MAX_LEVEL:
            raise ValueError(
                'Maximum filtering level ({}) exceeded: {}'.format(
                    self.MAX_LEVEL, pattern))

        return patterns + ([self.ALL_MATCH] * (self.MAX_LEVEL - len(patterns)))

    def filter_instance(self, instance):
        return fnmatch.fnmatch(instance.name, self.test_pattern)

    def filter_testsuite(self, testsuite):
        return fnmatch.fnmatch(
            get_testsuite_name(testsuite), self.suite_pattern)

    def filter_testcase(self, testcase):
        return fnmatch.fnmatch(
            testcase.__name__, self.testcase_pattern)

    @classmethod
    def any(cls, *patterns):
        """
            Shortcut for filtering against multiple patterns.

            e.g. Pattern.any(<pattern 1>, <pattern 2>...)
        """
        return Or(*[Pattern(pattern=pattern) for pattern in patterns])


class PatternAction(argparse.Action):
    """
        Parser action for generating Pattern filters.
        Returns a list of `Pattern` filter objects.

        In:

        --pattern foo bar --pattern baz

        Out:

        [Pattern('foo'), Pattern('bar'), Pattern('baz')]
    """
    def __call__(self, parser, namespace, values, option_string=None):
        items = getattr(namespace, self.dest) or []

        items.extend([Pattern(value) for value in values])
        setattr(namespace, self.dest, items)


class TagsAction(argparse.Action):
    """
        Parser action for generating tags (any) filters.

        In:

            --tags foo bar hello=world --tags baz hello=mars

        Out:

            [
                Tags({
                    'simple': frozenset({'foo', 'bar'}),
                    'hello': frozenset({'world'}),
                }),
                Tags({
                    'simple': frozenset({'baz'}),
                    'hello': frozenset({'mars'}),
                })
            ]

    """

    filter_class = Tags

    def __call__(self, parser, namespace, values, option_string=None):
        items = getattr(namespace, self.dest) or []
        items.append(self.filter_class(tagging.parse_tag_arguments(*values)))
        setattr(namespace, self.dest, items)


class TagsAllAction(TagsAction):
    """
        Parser action for generating tags (all) filters.

        In:

            --tags-all foo bar hello=world --tags-all baz hello=mars

        Out:

            [
                TagsAll({
                    'simple': frozenset({'foo', 'bar'}),
                    'hello': frozenset({'world'}),
                }),
                TagsAll({
                    'simple': frozenset({'baz'}),
                    'hello': frozenset({'mars'}),
                })
            ]
    """
    filter_class = TagsAll


def parse_filter_args(parsed_args, arg_names):
    """
        Utility function that's used for grouping filters of the same category
        together. Will be used while parsing command line arguments for
        test filters.

        Filters that belong to the same category will be grouped under `Or`
        whereas filters of different categories will be grouped under `And`.

        In:

            --pattern my_pattern --tags foo --tags-all bar baz

        Out:

            And(
                Pattern('my_pattern'),
                Or(
                    Tags({'simple': frozenset({'foo'})),
                    TagsAll({'simple': frozenset({'bar', 'baz'})),
                )
            )

    """
    def get_filter_category(filter_objs):
        if len(filter_objs) == 1:
            return filter_objs[0]
        return Or(*filter_objs)

    filter_dict = collections.defaultdict(list)

    for arg_name in arg_names:
        filters = parsed_args.get(arg_name)
        if filters:
            filter_dict[filters[0].category].extend(filters)

    # no arg_names are passed or parsed args is empty
    if not filter_dict:
        return None

    elif len(filter_dict) == 1:
        values = list(filter_dict.values())
        return get_filter_category(values[0])

    return And(
        *[get_filter_category(filters)
          for filters in filter_dict.values()]
    )