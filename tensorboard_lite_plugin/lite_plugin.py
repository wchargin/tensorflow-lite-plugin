# Copyright 2019 The TensorFlow Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================
"""Lite plugin."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import functools
import json
import mimetypes
import os

from werkzeug import wrappers

from tensorboard.backend import http_util
from tensorboard.backend.event_processing import plugin_event_accumulator as pec  # pylint: disable=line-too-long
from tensorboard.plugins import base_plugin

from tensorboard_lite_plugin import lite_backend

PLUGIN_PREFIX_ROUTE = "lite"
TAB_NAME = "Lite"
_LITE_DASHBOARD_FOLDER = "lite_dashboard"


class LitePlugin(base_plugin.TBPlugin):
  """A plugin that serves PR curves for individual classes."""

  plugin_name = PLUGIN_PREFIX_ROUTE

  def __init__(self, context):
    """Instantiates a LitePlugin.

    Args:
      context: A base_plugin.TBContext instance. A magic container that
        TensorBoard uses to make objects available to the plugin.
    """
    self._db_connection_provider = context.db_connection_provider
    self._multiplexer = context.multiplexer
    self._logdir = context.logdir

  def get_plugin_apps(self):
    """Gets all routes offered by the plugin.

    Returns:
      A dictionary mapping URL path to route that handles it.
    """
    return {
        "/list_supported_ops":
            self.list_supported_ops,
        "/list_saved_models":
            self.list_saved_models,
        "/convert":
            self.convert,
        "/script":
            self.script,
        "/index.html":
            functools.partial(
                self._serve_file,
                os.path.join(_LITE_DASHBOARD_FOLDER, "index.html")),
        "/index.js":
            functools.partial(self._serve_file,
                              os.path.join(_LITE_DASHBOARD_FOLDER, "index.js")),
    }

  @wrappers.Request.application
  def _serve_file(self, file_path, request):
    """Returns a resource file."""
    res_path = os.path.join(os.path.dirname(__file__), file_path)
    with open(res_path, "rb") as read_file:
      mimetype = mimetypes.guess_type(file_path)[0]
      return http_util.Respond(request, read_file.read(), content_type=mimetype)

  def is_active(self):
    """The graphs plugin is active iff any run has a graph."""
    if not lite_backend.is_supported:
      return False
    if not self._multiplexer:
      return False
    # Should contains some runs.
    run_names = [
        name for (name, data) in self._multiplexer.Runs().items()
        if data.get(pec.GRAPH)
    ]
    return any(run_names)

  @property
  def _tflite_output_dir(self):
    return os.path.join(self._logdir, "tflite_output")

  @property
  def _tflite_output_file(self):
    return os.path.join(self._tflite_output_dir, "model.tflite")

  def _generate_script(self, request, tflite_file):
    script = ""
    options = json.loads(request.form["data"])

    saved_model_dir = os.path.join(self._logdir, options["saved_model"] or "")
    input_arrays = options["input_arrays"] or []
    output_arrays = options["output_arrays"] or []
    script = lite_backend.script_from_saved_model(saved_model_dir, tflite_file,
                                                  input_arrays, output_arrays)
    return script

  @wrappers.Request.application
  def script(self, request):
    tflite_file = self._tflite_output_file
    script = self._generate_script(request, tflite_file)
    return http_util.Respond(request, json.dumps(script), "application/json")

  @wrappers.Request.application
  def convert(self, request):
    tflite_file = self._tflite_output_file
    script = self._generate_script(request, tflite_file)
    lite_backend.safe_makedirs(self._tflite_output_dir)

    success, stdout, stderr = lite_backend.execute(script)
    result = {}
    if success:
      result["result"] = "success"
      result["tabs"] = [{
          "name":
              "summary",
          "content": [{
              "type":
                  "text",
              "body":
                  u"Succuss: The model has been converted to tflite file.\n%s" %
                  lite_backend.to_unicode(stdout)
          }, {
              "type": "code",
              "body": tflite_file
          }]
      }, {
          "name": "command",
          "content": [{
              "type": "code",
              "body": script
          }]
      }]
    else:
      suggestion, tips_link = lite_backend.get_suggestion(stderr)

      result["result"] = "failed"
      result["tabs"] = [
          {
              "name":
                  "error",
              "content": [{
                  "type": "code",
                  "title": "error",
                  "body": u"%s" % lite_backend.to_unicode(stderr)
              },]
          },
      ]

      if suggestion:
        result["tabs"][0]["content"].append({
            "type": "text",
            "title": "Suggestion",
            "body": u"%s" % suggestion
        })

      result["tabs"].append({
          "name": "command",
          "content": [{
              "type": "code",
              "body": script
          }]
      })

      if tips_link:
        result["addons"] = [{
            "type": "link",
            "title": "Create a github issue",
            "body": tips_link
        }]

    return http_util.Respond(request, json.dumps(result), "application/json")

  @wrappers.Request.application
  def list_saved_models(self, request):
    saved_models = lite_backend.get_saved_model_dirs(self._logdir)
    return http_util.Respond(request, json.dumps(saved_models),
                             "application/json")

  @wrappers.Request.application
  def list_supported_ops(self, request):
    supported_ops = lite_backend.get_potentially_supported_ops()
    return http_util.Respond(request, supported_ops, "application/json")

  def frontend_metadata(self):
    return base_plugin.FrontendMetadata(
        tab_name=TAB_NAME,
        es_module_path="/index.js",
        disable_reload=False,
        remove_dom=False,
        element_name=None,
    )
