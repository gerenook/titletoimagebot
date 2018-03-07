""" - """

__version__ = '0.3'
__author__ = 'gerenook'

import logging
from datetime import datetime
from io import BytesIO
from logging.handlers import TimedRotatingFileHandler
from os import remove

import praw
import requests
from imgurpython import ImgurClient
from imgurpython.helpers.error import (ImgurClientError,
                                       ImgurClientRateLimitError)
from PIL import Image, ImageDraw, ImageFont

import apidata


class Meme:
    """ - """
    font_scale_factor = 20

    def __init__(self, image):
        """Initialize Meme instance
        :param image: the image (meme)
        :type image: PIL.Image.Image
        """
        self._meme = image
        self._width, self._height = image.size
        self._font = ImageFont.truetype('segoeui.ttf', self._width // Meme.font_scale_factor)
        self._font_author = ImageFont.truetype('segoeui.ttf', self._width // (Meme.font_scale_factor*2))

    def add_title(self, title):
        """Adds title to new whitespace on meme
        :param title: the title to add
        :type title: str
        """
        line_height = self._font.getsize(title)[1]
        texts = title.split(',')
        if not texts[-1]:
            texts = texts[:-1]
            texts[-1] += ','
        whitespace_height = (line_height * len(texts)) + 10
        new = Image.new('RGB', (self._width, self._height + whitespace_height), '#fff')
        new.paste(self._meme, (0, whitespace_height))
        draw = ImageDraw.Draw(new)
        for i, text in enumerate(texts):
            d = ''
            if i < len(texts)-1:
                d = ','
            draw.text((10, i * line_height), text.lstrip() + d, '#000', self._font)
        self._width, self._height = new.size
        self._meme = new

    def add_author(self, author):
        """Adds /u/author to top right of image
        :param author: the author to add (without /u/)
        :type author: str
        """
        text = '/u/' + author
        draw = ImageDraw.Draw(self._meme)
        size = self._font_author.getsize(text)
        pos = (self._width - (size[0] + 10), 0)
        draw.text(pos, text, '#000', self._font_author)

    def upload(self, imgur):
        """Uploads self._meme to imgur
        :param imgur: the imgur api client
        :type imgur: imgurpython.client.ImgurClient
        :returns: imgur url if upload successful, else None
        """
        path_png = 'temp.png'
        path_jpg = 'temp.jpg'
        self._meme.save(path_png)
        self._meme.save(path_jpg)
        try:
            response = imgur.upload_from_path(path_png)
        except (ImgurClientError, ImgurClientRateLimitError):
            # png upload failed, trying to upload jpg
            try:
                response = imgur.upload_from_path(path_jpg)
            except (ImgurClientError, ImgurClientRateLimitError):
                return None
        finally:
            remove(path_png)
            remove(path_jpg)
        return response['link']


class TitleToMemeBot:
    """ - """
    def __init__(self, imgur, reddit):
        """ - """
        self._imgur = imgur
        self._reddit = reddit

    def _process_submission(self, submission):
        """ - """
        pass

    def _check_messages(self):
        """ - """
        pass

    def run(self, test=False):
        """Start the bot
        :param test: if true, subreddit 'testingground4bots' is included
        :type test: bool
        """
        sub = 'boottoobig'
        if test:
            sub += '+testingground4bots'
        subreddit = self._reddit.subreddit(sub)
        for i, submission in enumerate(subreddit.stream.submissions()):
            # stream includes past 100 submissions, skip those
            if i < 100:
                continue
            self._process_submission(submission)
            self._check_messages()

def setup_logger():
    """ - """
    logger = logging.getLogger()
    handler = TimedRotatingFileHandler('./log/titletomemebot.log', when='midnight', interval=1)
    handler.setLevel(logging.DEBUG)
    handler.suffix = '%Y-%m-%d'
    log_format = '%(asctime)s %(levelname)s:%(funcName)s:%(message)s'
    formatter = logging.Formatter(log_format)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

def main():
    """Main function"""
    setup_logger()
    imgur = ImgurClient(**apidata.imgur)
    reddit = praw.Reddit(**apidata.reddit)
    bot = TitleToMemeBot(imgur, reddit)
    bot.run()

if __name__ == '__main__':
    main()
