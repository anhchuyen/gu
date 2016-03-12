from django.shortcuts import get_object_or_404, render, redirect
from django.core.urlresolvers import reverse
from django.http import Http404
from django.http import HttpResponseRedirect
from django.views import generic
from django.views.generic import View
from django.db.models import Q
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.sites.models import Site
from django.utils.html import strip_tags
from django.http import HttpResponseForbidden
from django.views.decorators.http import last_modified
from django.utils import timezone
from django.http import JsonResponse

import datetime
import logging
from random import randint

from . import models
from . import settings
from . import utils
from .forms import ArticleListForm, ArticleForm


from blocks.models import Group

logger = logging.getLogger(__name__)


def get_blocks(group_name="Home"):
    try:
        return Group.objects.get(name=group_name).get_blocks()
    except:
        return []


def get_blocks_in_context(context, group_name="Home"):
    context["blocks"] = get_blocks(group_name)
    return context


class ArticleList(generic.ListView):
    context_object_name = 'article_list'
    template_name = "newsroom/article_list.html"
    paginate_by = settings.ARTICLES_PER_PAGE

    def get_queryset(self):
        return models.Article.objects.list_view()

    def get_context_data(self, **kwargs):
        context = super(ArticleList, self).get_context_data(**kwargs)
        context = get_blocks_in_context(context)
        return context


def last_article_modified(request):
    return models.Article.objects.published().latest("modified").modified


class HomePage(ArticleList):
    template_name = "newsroom/home.html"

    def get_context_data(self, **kwargs):
        context = super(HomePage, self).get_context_data(**kwargs)
        # Add extra context for the home page here
        context['most_popular_html'] = \
            models.MostPopular.get_most_popular_html()
        return context

    # LEAVE THIS COMMENTED OUT CODE IN CASE OF EMERGENCY IN
    # WHICH CODE NEEDS TO CHANGE URGENTLY.

    # def get(self, request, *args, **kwargs):
    #       # Add messages here. E.g.
    #       messages.add_message(request, messages.INFO,
    #        "We are closed <a href='#'>until 5 January</a>.")
    #       request = super(HomePage, self).get(request, args, kwargs)
    #       return request

home_page_view = HomePage.as_view()
home_page_view = last_modified(last_article_modified)(home_page_view)


class OpinionAnalysisList(ArticleList):

    def get_queryset(self):
        return models.Article.objects.list_view().filter(
            Q(category__name="Opinion") |
            Q(category__name="Analysis"))

    def get_context_data(self, **kwargs):
        context = super(OpinionAnalysisList, self).get_context_data(**kwargs)
        context['heading'] = "Opinion and Analysis"
        return context


class AuthorDetail(ArticleList):

    def get_queryset(self):
        self.author = get_object_or_404(models.Author, pk=self.args[0])
        return models.Article.objects.list_view().filter(
            Q(author_01=self.author) | Q(author_02=self.author) |
            Q(author_03=self.author) | Q(author_04=self.author) |
            Q(author_05=self.author) |
            Q(cached_byline_no_links__icontains=str(self.author)))

    def get_context_data(self, **kwargs):
        context = super(AuthorDetail, self).get_context_data(**kwargs)
        context['heading'] = "Articles by " + str(self.author)
        context['image'] = self.author.photo
        context['description'] = self.author.description
        return context


class CategoryDetail(ArticleList):

    def get_queryset(self):
        self.category = get_object_or_404(models.Category,
                                          name__iexact=self.args[0])
        return models.Article.objects.list_view(). \
            filter(category=self.category)

    def get_context_data(self, **kwargs):
        context = super(CategoryDetail, self).get_context_data(**kwargs)
        context['heading'] = self.category.name
        return context


class RegionDetail(ArticleList):

    def get_queryset(self):
        self.region = get_object_or_404(models.Region, name=self.args[0])
        return models.Article.objects.list_view(). \
            filter(Q(region__in=self.region.get_descendants()) |
                   Q(region=self.region))

    def get_context_data(self, **kwargs):
        context = super(RegionDetail, self).get_context_data(**kwargs)
        query = Q(name=self.region.name)
        region_name = str(self.region)
        while region_name:
            region_name = region_name.rpartition("/")[0]
            query = query | Q(name=region_name)
        regions = models.Region.objects.filter(query)
        regions = ["<a href='" + reverse("region.detail",
                                         args=(region.name, )) + "'>"
                   + region.name.rpartition("/")[2] + "</a>"
                   for region in regions]
        context['heading'] = "|".join(regions)
        return context


class TopicDetail(ArticleList):

    def get_queryset(self):
        self.topic = get_object_or_404(models.Topic, slug=self.args[0])
        return models.Article.objects.published(). \
            filter(topics=self.topic)

    def get_context_data(self, **kwargs):
        context = super(TopicDetail, self).get_context_data(**kwargs)
        context['heading'] = self.topic.name
        context['topic'] = self.topic
        return context

    def get_template_names(self):
        return (self.topic.template,)


# Support functions for article editing

def check_concurrent_edit(request):
    '''This is an Ajax callback on article update pages to
    check if another user has updated the article.
    '''
    if request.method == "POST" and \
       request.is_ajax and \
       request.user.has_perm("newsroom.change_article"):
        pk = int(request.POST["pk"])
        version = int(request.POST["version"])
        article = get_object_or_404(models.Article, pk=pk)
        if article.version > version:
            edited_by = str(article.user)
        else:
            edited_by = "(None)"
        try:
            user_edit = models.UserEdit.objects.get(
                article__pk=article.pk,
                user=request.user)
        except models.UserEdit.DoesNotExist:
            user_edit = models.UserEdit()
            user_edit.article = article
            user_edit.user = request.user
        finally:
            user_edit.save()
        cutoff = timezone.now() - datetime.timedelta(seconds=30)
        user_edits = models.UserEdit.objects. \
            filter(article=article). \
            exclude(user=request.user). \
            filter(edit_time__gte=cutoff)
        users = [str(obj.user) for obj in user_edits]
        return JsonResponse({
            'edited_by': edited_by,
            'users': users
        }, safe=False)
    else:
        raise Http404


# These functions were originally two generic Django class views (DetailView and
# UpdateView). But the logic was hidden behind Django's opaque and not very well
# documented system. This might seem complex with lots of coding that Django
# could take care of, but at least I can understand what's going on here without
# having to search reams of documentation and StackOverview questions.
#
# Nevertheless, some refactoring needed here.

def article_post(request, slug):
    form = ArticleForm(request.POST)
    if form.is_valid():
        if request.user.has_perm("newsroom.change_article"):
            article = get_object_or_404(models.Article, slug=slug)
            if article.version > form.cleaned_data["version"]:
                messages.add_message(request, messages.ERROR,
                                     utils.get_edit_lock_msg(article.user))
                for field in form.changed_data:
                    setattr(article, field,
                            form.cleaned_data[field])
                    return render(request, article.template,
                                  {'article': article,
                                   'display_region': None,
                                   'see_also': None,
                                   'read_next': None,
                                   'blocks': get_blocks(),
                                   'can_edit': False,
                                   'most_popular_html': None,
                                   'form': form})
            for field in form.cleaned_data:
                setattr(article, field, form.cleaned_data[field])
            article.user = request.user
            article.save()
            # Check if user clicked "Publish" button
            if request.POST["is_published"] == "Now":
                article.publish_now()
                messages.add_message(request, messages.INFO,
                                     "Article published.")
            else:
                messages.add_message(request, messages.INFO,
                                     "Changes saved.")
            return HttpResponseRedirect(reverse('article.detail',
                                                args=(slug,)))
        else:
            return HttpResponseForbidden()
    else:
        if form.data["title"] == "":
            messages.add_message(request, messages.ERROR,
                                 "Title can't be blank.")
        else:
            messages.add_message(request, messages.ERROR,
                                 "Something went wrong.")
        return HttpResponseRedirect(reverse('article.detail',
                                            args=(slug,)))


def article_detail(request, slug):
    if request.method == 'POST':
        return article_post(request, slug)
    else:  # GET
        article = get_object_or_404(models.Article, slug=slug)
        if request.user.has_perm('newsroom.change_article'):
            form = ArticleForm(instance=article)
        else:
            form = None
        if article.is_published() or request.user.is_staff:
            if request.user.is_staff and not article.is_published():
                messages.add_message(request, messages.INFO,
                                     "This article is not published.")

            if article.region and (article.region.name not in
                                   ["None", "", "(None)", ]):
                display_region = article.region.name.rpartition("/")[2]
            else:
                display_region = ""

            try:
                read_next = models.Article.objects.published().\
                    exclude(pk=article.pk). \
                    exclude(recommended=False)[randint(0, 9)]
                read_next_pk = read_next.pk
            except IndexError:
                read_next = None
                read_next_pk = article.pk

            if article.main_topic:
                see_also = models.Article.objects.published(). \
                    filter(topics=article.main_topic). \
                    exclude(pk=article.pk).exclude(pk=read_next_pk).\
                    exclude(recommended=False).distinct()[0:4]
            elif article.topics:
                see_also = models.Article.objects.published(). \
                    filter(topics=article.topics.all()). \
                    exclude(pk=article.pk).exclude(pk=read_next_pk). \
                    exclude(recommended=False).distinct()[0:4]
            else:
                see_also = None

            can_edit = False
            if request.user.is_staff and \
               request.user.has_perm("newsroom.change_article"):
                can_edit = True
                query_edit = request.GET.get('edit', "true")
                if query_edit.lower() == "no" or \
                   query_edit.lower() == "false":
                    can_edit = False

            if not request.user.is_authenticated():
                article_body = article.body.replace(
                    '<aside class="article-advert-edit">',
                    '<aside class="article-advert">' + settings.ADVERT_CODE)
            else:
                if can_edit:
                    article_body = article.body
                else:
                    article_body = article.body.replace(
                        '<aside class="article-advert-edit">',
                        '<aside class="article-advert" style="display:none;">')

            return render(request, article.template,
                          {'article': article,
                           'display_region': display_region,
                           'see_also': see_also,
                           'read_next': read_next,
                           'blocks': get_blocks(),
                           'can_edit': can_edit,
                           'article_body': article_body,
                           'most_popular_html':
                           models.MostPopular.get_most_popular_html,
                           'form': form})
        else:
            raise Http404

####################################################

''' Redirect images on old Drupal site
'''


class RedirectOldImages(View):

    def get(self, request, path):
        url = "/media/old/" + path
        return redirect(url)

'''Redirect hand constructed features (mainly an old site phenomenon

'''


class RedirectHandConstructedFeatures(View):

    def get(self, request, path):
        url = "/media/features/" + path
        return redirect(url)

'''Redirect old /content/ articles to new /article/

'''


class RedirectContentToArticle(View):

    def get(self, request, path):
        url = "/article/" + path
        return redirect(url)


'''newsletter generator form
'''


@staff_member_required
def generate_article_list(request):
    output = []
    articles = []
    if request.method == "POST":
        form = ArticleListForm(request.POST)
        if form.is_valid():
            date_from = form.cleaned_data["date_from"]
            date_to = form.cleaned_data["date_to"]
            if date_to:
                articles = models.Article.objects.published(). \
                    filter(published__gte=date_from). \
                    filter(published__lte=date_to)
            else:
                articles = models.Article.objects.published(). \
                    filter(published__gte=date_from)
            site_url = 'http://' + Site.objects.get_current().domain
            for article in articles:
                output.append("<h3><a href='" + site_url +
                              article.get_absolute_url() +
                              "'>" + article.title + "</a></h3>")
                output.append(
                    "<p>" + strip_tags(article.cached_summary_text) + "</p>")
                output.append("<p style='font-style:italic;'>" +
                              article.cached_byline_no_links + "</p>\n")
    else:
        form = ArticleListForm()

    return render(request, "newsroom/article_list_form.html",
                  {'form': form,
                   'output': output,
                   'len_articles': len(articles)})

''' Used to test logging
'''


def testLoggingDebug():
    logger.debug("Debug logging test")


def testLoggingInfo():
    logger.debug("Info logging test")


def testLoggingWarning():
    logger.debug("Warning logging test")


def testLoggingError():
    logger.error("Error logging test")


def testLoggingCritical():
    logger.critical("Critical logging test")


def testLoggingAll():
    testLoggingDebug()
    testLoggingInfo()
    testLoggingWarning()
    testLoggingError()
    testLoggingCritical()
