#!/usr/bin/env python3

""" - """

__version__ = '0.3'
__author__ = 'gerenook'

import logging
from datetime import datetime
from io import BytesIO
from logging.handlers import TimedRotatingFileHandler
from math import ceil
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
    font_file = 'segoeui.ttf'
    font_scale_factor = 25

    def __init__(self, image):
        """Initialize Meme instance
        :param image: the image (meme)
        :type image: PIL.Image.Image
        """
        self._meme = image
        self._width, self._height = image.size

    def add_title(self, title, poem=False):
        """Add title to new whitespace on meme
        :param title: the title to add
        :type title: str
        """
        font = ImageFont.truetype(Meme.font_file, self._width // Meme.font_scale_factor)
        line_height = font.getsize(title)[1]        
        texts = []
        if poem:
            texts = title.split(',')
            if not texts[-1]:
                texts = texts[:-1]
                texts[-1] += ','
        else:
            limit = 45
            lines = ceil(len(title) / limit)
            if lines > 1:
                words = title.split(' ')
                x = ceil(len(words) / lines)
                for l in range(lines):
                    texts.append(' '.join(words[l*x:(l+1)*x]))
            else:
                texts.append(title)
        whitespace_height = (line_height * len(texts)) + 10
        new = Image.new('RGB', (self._width, self._height + whitespace_height), '#fff')
        new.paste(self._meme, (0, whitespace_height))
        draw = ImageDraw.Draw(new)
        for i, text in enumerate(texts):
            d = ''
            if i < len(texts)-1:
                d = ','
            draw.text((10, i * line_height), text.lstrip() + d, '#000', font)
        self._width, self._height = new.size
        self._meme = new

    def add_author(self, author):
        """Add /u/author to top right of image
        :param author: the author to add (without /u/)
        :type author: str
        """
        font = ImageFont.truetype(Meme.font_file, self._width // (Meme.font_scale_factor*2))
        text = '/u/' + author
        draw = ImageDraw.Draw(self._meme)
        size = font.getsize(text)
        pos = (self._width - (size[0] + 10), 0)
        draw.text(pos, text, '#000', font)

    def upload(self, imgur):
        """Upload self._meme to imgur
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
            # png upload failed (probably >10MiB), trying to upload jpg
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
    _templates = {
        'submission': '[Image with title]({0})\n\n' \
                      '---\n\n' \
                      '^^Did ^^I ^^fuck ^^up? ^^[remove](https://reddit.com/message/compose/?to=TitleToMemeBot&subject=remove&message={1}) ^^| ' \
                      '^^[feedback](https://reddit.com/message/compose/?to=TitleToMemeBot&subject=feedback)',
        'feedback': 'Thanks for your feedback, I forwarded it to my creator!'
    }

    def __init__(self, imgur, reddit):
        """ - """
        self._imgur = imgur
        self._reddit = reddit

    def _process_submission(self, submission):
        """Generate new image with added title and author, upload to imgur, reply to submission
        :param submission: the reddit submission object
        :type submission: praw.models.reddit.submission.Submission
        """
        # dtprint('Found new submission ({0}), downloading image'.format(submission.id))
        response = requests.get(submission.url)
        try:
            img = Image.open(BytesIO(response.content))
        except OSError as error:
            # dtprint(error)
            response = requests.get(submission.url + '.jpg')
            try:
                img = Image.open(BytesIO(response.content))
            except OSError as error1:
                # dtprint(error1)
                return
        title = submission.title
        boot = False
        if submission.subreddit.display_name == 'boottoobig':
            boot = True
        if boot:
            triggers = [',', '.', 'roses']
            if not any(t in title.lower() for t in triggers):
                # dtprint('Title is probably not part of rhyme, returning')
                return
        meme = Meme(img)
        # dtprint('Adding title and author to image')
        meme.add_title(title, boot)
        meme.add_author(submission.author.name)
        # dtprint('Uploading image')
        for _ in range(3):
            url = meme.upload(self._imgur)
            if url:
                break
            # dtprint('Upload failed, retrying')
        if not url:
            # dtprint('Can\'t upload image, returning')
            return
        # dtprint('Creating reply')
        reply = TitleToMemeBot._templates['submission'].format(url, '{0}')
        try:
            comment = submission.reply(reply)
        except Exception as error:
            # dtprint(error)
            return
        # dtprint('Editing comment with remove link')
        comment.edit(reply.format(comment.id))
        # dtprint('Done :)')

    def _process_remove_message(self, message):
        """Remove comment referenced in message body
        :param message: the remove message
        :type message: praw.models.reddit.message.Message
        """
        # dtprint('Found remove message, trying to remove')
        comment_id = message.body
        try:
            comment = self._reddit.comment(comment_id)
            submission_author = comment.submission.author.name
            message_author = message.author.name
            if (message_author == submission_author or
                    message_author == __author__):
                comment.delete()
                # dtprint('Done :)')
            else:
                # dtprint('Authors don\'t match, not removing')
                pass
            message.mark_read()
        except Exception as error:
            # dtprint(error)
            pass

    def _process_feedback_message(self, message):
        """Forward message to creator, send comfirm message to message author
        :param message: the feedback message
        :type message: praw.models.reddit.message.Message
        """
        # dtprint('Found feedback message, forwarding to author')
        subject = 'TitleToMemeBot feedback from ' + message.author.name
        body = message.body
        self._reddit.redditor(__author__).message(subject, body)
        message.mark_read()

        subject = 'TitleToMemeBot feedback'
        body = TitleToMemeBot._templates['feedback']
        message.author.message(subject, body)
        # dtprint('Done :)')

    def _check_messages(self):
        """Check inbox for remove and feedback messages
        :param reddit: the reddit object
        :type reddit: praw.reddit.Reddit
        """
        inbox = self._reddit.inbox
        # dtprint('Checking unread messages')
        for message in inbox.unread(limit=None):
            if message.subject == 'remove':
                self._process_remove_message(message)
            elif message.subject == 'feedback':
                self._process_feedback_message(message)

    def run(self, test=False):
        """Start the bot
        :param test: if true, subreddit 'testingground4bots' is included
        :type test: bool
        """
        sub = 'boottoobig+fakehistoryporn'
        if test:
            sub += '+testingground4bots'
        subreddit = self._reddit.subreddit(sub)
        while True:
            try:
                for i, submission in enumerate(subreddit.stream.submissions()):
                    # stream includes past 100 submissions, skip those
                    if i < 100:
                        continue
                    self._process_submission(submission)
                    self._check_messages()
            except requests.exceptions.ReadTimeout:
                continue


def setup_logger():
    """ - """
    logger = logging.getLogger()
    log_format = '%(asctime)s %(levelname)s:%(funcName)s:%(message)s'
    formatter = logging.Formatter(log_format)

    file_handler = TimedRotatingFileHandler('./log/titletomemebot.log', when='midnight', interval=1)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.suffix = '%Y-%m-%d'
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

def main():
    """Main function"""
    setup_logger()
    imgur = ImgurClient(**apidata.imgur)
    reddit = praw.Reddit(**apidata.reddit)
    bot = TitleToMemeBot(imgur, reddit)
    bot.run()

if __name__ == '__main__':
    main()
