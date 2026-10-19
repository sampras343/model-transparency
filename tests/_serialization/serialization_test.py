# Copyright 2024 The Sigstore Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for shared serialization checks."""

import errno
import os
import pathlib
from unittest import mock

import pytest

from model_signing._serialization import serialization


@pytest.mark.parametrize("kind", ["runtime", "posix", "windows"])
def test_native_symlink_loop_warns_and_skips(tmp_path, caplog, kind):
    """Recognize loop errors across supported platforms and Python versions."""
    if kind == "runtime":
        error = RuntimeError("Symlink loop")
    elif kind == "posix":
        error = OSError(errno.ELOOP, "Too many levels of symbolic links")
    else:
        error = OSError(errno.EINVAL, "Cannot resolve filename")
        error.winerror = 1921

    with mock.patch.object(pathlib.Path, "resolve", side_effect=error):
        assert not serialization.check_symlink(tmp_path / "link", tmp_path)
    assert "Symlink cycle detected" in caplog.text


@pytest.mark.parametrize(
    "error_number", [errno.ENOENT, errno.EACCES, errno.EINVAL]
)
def test_other_resolution_errors_are_rejected(tmp_path, caplog, error_number):
    """Reject missing, inaccessible, and otherwise unusable targets."""
    error = OSError(error_number, "Cannot resolve link")
    with (
        mock.patch.object(pathlib.Path, "resolve", side_effect=error),
        pytest.raises(ValueError, match="broken symlink") as raised,
    ):
        serialization.check_symlink(tmp_path / "link", tmp_path)
    assert raised.value.__cause__ is error
    assert "Symlink cycle detected" not in caplog.text


def test_cycle_in_directory_component_warns_and_skips(tmp_path, caplog):
    """Detect loops before the final component of a target path."""
    (tmp_path / "loop").symlink_to("loop", target_is_directory=True)
    link = tmp_path / "link"
    link.symlink_to("loop/file")

    assert not serialization.check_symlink(link, tmp_path)
    assert "Symlink cycle detected" in caplog.text


def test_parent_traversal_through_symlink_rejects_missing_target(
    tmp_path, caplog
):
    """Resolve directory links before interpreting parent traversal."""
    model = tmp_path / "model"
    model.mkdir()
    nested = tmp_path / "outside" / "nested"
    nested.mkdir(parents=True)
    (model / "sub").symlink_to(nested, target_is_directory=True)
    link = model / "link"
    link.symlink_to("sub/../link")

    with pytest.raises(ValueError, match="broken symlink"):
        serialization.check_symlink(link, model)
    assert "Symlink cycle detected" not in caplog.text


@pytest.mark.skipif(os.name == "nt", reason="Windows limits symlink hops")
def test_long_dangling_chain_is_rejected(tmp_path, caplog):
    """Do not mistake an arbitrary hop count for a cycle."""
    for index in range(65):
        (tmp_path / f"link{index}").symlink_to(f"link{index + 1}")

    with pytest.raises(ValueError, match="broken symlink"):
        serialization.check_symlink(tmp_path / "link0", tmp_path)
    assert "Symlink cycle detected" not in caplog.text
