from django.db import models
from newsroom.models import Article
from filebrowser.fields import FileBrowseField
import tagulous
from .common import SCHEDULE_RESULTS

# Create your models here.


class TwitterHandle(tagulous.models.TagModel):

    class TagMeta:
        case_sensitive = False
        max_count=8
        space_delimiter = True
        protect_all = True

    def __str__(self):
        return self.name

def calc_chars_left(tweet_text, image, tags):
    chars_left = 117 - len(tweet_text.strip())
    if image:
        chars_left = chars_left - 23
        for account in tags:
            chars_left = chars_left - len(account.strip()) - 2
    return chars_left

class Tweet(models.Model):
    article = models.ForeignKey(Article)
    wait_time = models.PositiveIntegerField(help_text = \
                                            "Number of minutes "
                                            "after publication "
                                            "till tweet.")
    status = models.CharField(max_length=20,
                              choices=SCHEDULE_RESULTS,
                              default="scheduled")
    tweet_text = models.CharField(max_length=117, blank=True)
    image = FileBrowseField(max_length=200, directory="images/",
                            blank=True, null=True)
    tag_accounts = tagulous.models.TagField(
        to=TwitterHandle,
        blank=True,
        help_text=""
    )
    characters_left = models.IntegerField(default=117)

    class Meta:
        ordering = ["article__published", "wait_time",]

    def __str__(self):
        return self.article.title + ": " + str(self.wait_time)

    def save(self, *args, **kwargs):
        super(Tweet, self).save(*args, **kwargs)
        twitter_handles = [str(name) for name in self.tag_accounts.all()]
        self.characters_left = calc_chars_left(self.tweet_text, \
                                               self.image, \
                                               twitter_handles)
        super(Tweet, self).save(force_update=True, *args, **kwargs)
