# Copyright The PyTorch Lightning team.
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
"""
Model Checkpointing
===================

Automatically save model checkpoints during training.

"""
import logging
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Optional, Union

import numpy as np
import torch
import yaml

from pytorch_lightning.callbacks.base import Callback
from pytorch_lightning.utilities import rank_zero_deprecation, rank_zero_info, rank_zero_only, rank_zero_warn
from pytorch_lightning.utilities.cloud_io import get_filesystem
from pytorch_lightning.utilities.exceptions import MisconfigurationException
from pytorch_lightning.utilities.warnings import WarningCache

log = logging.getLogger(__name__)
warning_cache = WarningCache()


class ModelCheckpoint(Callback):
    r"""
    Save the model after every epoch by monitoring a quantity.

    After training finishes, use :attr:`best_model_path` to retrieve the path to the
    best checkpoint file and :attr:`best_model_score` to retrieve its score.

    Args:
        dirpath: directory to save the model file.

            Example::

                # custom path
                # saves a file like: my/path/epoch=0-step=10.ckpt
                >>> checkpoint_callback = ModelCheckpoint(dirpath='my/path/')

            By default, dirpath is ``None`` and will be set at runtime to the location
            specified by :class:`~pytorch_lightning.trainer.trainer.Trainer`'s
            :paramref:`~pytorch_lightning.trainer.trainer.Trainer.default_root_dir` or
            :paramref:`~pytorch_lightning.trainer.trainer.Trainer.weights_save_path` arguments,
            and if the Trainer uses a logger, the path will also contain logger name and version.

        filename: checkpoint filename. Can contain named formatting options to be auto-filled.

            Example::

                # save any arbitrary metrics like `val_loss`, etc. in name
                # saves a file like: my/path/epoch=2-val_loss=0.02-other_metric=0.03.ckpt
                >>> checkpoint_callback = ModelCheckpoint(
                ...     dirpath='my/path',
                ...     filename='{epoch}-{val_loss:.2f}-{other_metric:.2f}'
                ... )

            By default, filename is ``None`` and will be set to ``'{epoch}-{step}'``.
        monitor: quantity to monitor. By default it is ``None`` which saves a checkpoint only for the last epoch.
        verbose: verbosity mode. Default: ``False``.
        save_last: When ``True``, always saves the model at the end of the epoch to
            a file `last.ckpt`. Default: ``None``.
        save_top_k: if ``save_top_k == k``,
            the best k models according to
            the quantity monitored will be saved.
            if ``save_top_k == 0``, no models are saved.
            if ``save_top_k == -1``, all models are saved.
            Please note that the monitors are checked every ``period`` epochs.
            if ``save_top_k >= 2`` and the callback is called multiple
            times inside an epoch, the name of the saved file will be
            appended with a version count starting with ``v1``.
        mode: one of {min, max}.
            If ``save_top_k != 0``, the decision to overwrite the current save file is made
            based on either the maximization or the minimization of the monitored quantity.
            For ``'val_acc'``, this should be ``'max'``, for ``'val_loss'`` this should be ``'min'``, etc.
        save_weights_only: if ``True``, then only the model's weights will be
            saved (``model.save_weights(filepath)``), else the full model
            is saved (``model.save(filepath)``).
        every_n_train_steps: Number of training steps between checkpoints.
            If ``every_n_train_steps == None or every_n_train_steps == 0``, we skip saving during training
            To disable, set ``every_n_train_steps = 0``. This value must be ``None`` non-negative.
            This must be mutually exclusive with ``every_n_val_epochs``.
        every_n_val_epochs: Number of validation epochs between checkpoints.
            If ``every_n_val_epochs == None or every_n_val_epochs == 0``, we skip saving on validation end
            To disable, set ``every_n_val_epochs = 0``. This value must be ``None`` or non-negative.
            This must be mutually exclusive with ``every_n_train_steps``.
            Setting both ``ModelCheckpoint(..., every_n_val_epochs=V)`` and
            ``Trainer(max_epochs=N, check_val_every_n_epoch=M)``
            will only save checkpoints at epochs 0 < E <= N
            where both values for ``every_n_val_epochs`` and ``check_val_every_n_epoch`` evenly divide E.
        period: Interval (number of epochs) between checkpoints.

            .. warning::
               This argument has been deprecated in v1.3 and will be removed in v1.5.

            Use ``every_n_val_epochs`` instead.

    Note:
        For extra customization, ModelCheckpoint includes the following attributes:

        - ``CHECKPOINT_JOIN_CHAR = "-"``
        - ``CHECKPOINT_NAME_LAST = "last"``
        - ``FILE_EXTENSION = ".ckpt"``
        - ``STARTING_VERSION = 1``

        For example, you can change the default last checkpoint name by doing
        ``checkpoint_callback.CHECKPOINT_NAME_LAST = "{epoch}-last"``

    Raises:
        MisconfigurationException:
            If ``save_top_k`` is neither ``None`` nor more than or equal to ``-1``,
            if ``monitor`` is ``None`` and ``save_top_k`` is none of ``None``, ``-1``, and ``0``, or
            if ``mode`` is none of ``"min"`` or ``"max"``.
        ValueError:
            If ``trainer.save_checkpoint`` is ``None``.

    Example::

        >>> from pytorch_lightning import Trainer
        >>> from pytorch_lightning.callbacks import ModelCheckpoint

        # saves checkpoints to 'my/path/' at every epoch
        >>> checkpoint_callback = ModelCheckpoint(dirpath='my/path/')
        >>> trainer = Trainer(callbacks=[checkpoint_callback])

        # save epoch and val_loss in name
        # saves a file like: my/path/sample-mnist-epoch=02-val_loss=0.32.ckpt
        >>> checkpoint_callback = ModelCheckpoint(
        ...     monitor='val_loss',
        ...     dirpath='my/path/',
        ...     filename='sample-mnist-{epoch:02d}-{val_loss:.2f}'
        ... )

        # save epoch and val_loss in name, but specify the formatting yourself (e.g. to avoid problems with Tensorboard
        # or Neptune, due to the presence of characters like '=' or '/')
        # saves a file like: my/path/sample-mnist-epoch02-val_loss0.32.ckpt
        >>> checkpoint_callback = ModelCheckpoint(
        ...     monitor='val/loss',
        ...     dirpath='my/path/',
        ...     filename='sample-mnist-epoch{epoch:02d}-val_loss{val/loss:.2f}',
        ...     auto_insert_metric_name=False
        ... )

        # retrieve the best checkpoint after training
        checkpoint_callback = ModelCheckpoint(dirpath='my/path/')
        trainer = Trainer(callbacks=[checkpoint_callback])
        model = ...
        trainer.fit(model)
        checkpoint_callback.best_model_path

    """

    CHECKPOINT_JOIN_CHAR = "-"
    CHECKPOINT_NAME_LAST = "last"
    FILE_EXTENSION = ".ckpt"
    STARTING_VERSION = 1

    def __init__(
        self,
        dirpath: Optional[Union[str, Path]] = None,
        filename: Optional[str] = None,
        monitor: Optional[str] = None,
        verbose: bool = False,
        save_last: Optional[bool] = None,
        save_top_k: Optional[int] = None,
        save_weights_only: bool = False,
        mode: str = "min",
        auto_insert_metric_name: bool = True,
        every_n_train_steps: Optional[int] = None,
        every_n_val_epochs: Optional[int] = None,
        period: Optional[int] = None,
    ):
        super().__init__()
        self.monitor = monitor
        self.verbose = verbose
        self.save_last = save_last
        self.save_top_k = save_top_k
        self.save_weights_only = save_weights_only
        self.auto_insert_metric_name = auto_insert_metric_name
        self._last_global_step_saved = -1
        self.current_score = None
        self.best_k_models = {}
        self.kth_best_model_path = ""
        self.best_model_score = None
        self.best_model_path = ""
        self.last_model_path = ""
        self.save_function = None
        self.warned_result_obj = False

        self.__init_monitor_mode(monitor, mode)
        self.__init_ckpt_dir(dirpath, filename, save_top_k)
        self.__init_triggers(every_n_train_steps, every_n_val_epochs, period)
        self.__validate_init_configuration()

    def on_pretrain_routine_start(self, trainer, pl_module):
        """
        When pretrain routine starts we build the ckpt dir on the fly
        """
        self.__resolve_ckpt_dir(trainer)
        self.save_function = trainer.save_checkpoint

    def on_train_batch_end(self, trainer, *args, **kwargs) -> None:
        """ Save checkpoint on train batch end if we meet the criteria for `every_n_train_steps` """
        if self._should_skip_saving_checkpoint(trainer):
            return
        step = trainer.global_step
        skip_batch = self._every_n_train_steps < 1 or ((step + 1) % self._every_n_train_steps != 0)
        if skip_batch:
            return
        self.save_checkpoint(trainer)

    def on_validation_end(self, trainer, *args, **kwargs) -> None:
        """
        checkpoints can be saved at the end of the val loop
        """
        skip = (
            self._should_skip_saving_checkpoint(trainer) or self._every_n_val_epochs < 1
            or (trainer.current_epoch + 1) % self._every_n_val_epochs != 0
        )
        if skip:
            return
        self.save_checkpoint(trainer)

    def on_save_checkpoint(self, trainer, pl_module, checkpoint: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "monitor": self.monitor,
            "best_model_score": self.best_model_score,
            "best_model_path": self.best_model_path,
            "current_score": self.current_score,
            "dirpath": self.dirpath
        }

    def on_load_checkpoint(self, callback_state: Dict[str, Any]):
        self.best_model_score = callback_state["best_model_score"]
        self.best_model_path = callback_state["best_model_path"]

    def save_checkpoint(self, trainer, unused: Optional = None):
        """
        Performs the main logic around saving a checkpoint.
        This method runs on all ranks, it is the responsibility of `self.save_function`
        to handle correct behaviour in distributed training, i.e., saving only on rank 0.
        """
        if unused is not None:
            rank_zero_deprecation(
                "`ModelCheckpoint.save_checkpoint` signature has changed in v1.3. The `pl_module` parameter"
                " has been removed. Support for the old signature will be removed in v1.5"
            )

        global_step = trainer.global_step

        self._add_backward_monitor_support(trainer)
        self._validate_monitor_key(trainer)

        # track epoch when ckpt was last checked
        self._last_global_step_saved = global_step

        # what can be monitored
        monitor_candidates = self._monitor_candidates(trainer)

        # callback supports multiple simultaneous modes
        # here we call each mode sequentially
        # Mode 1: save the top k checkpoints
        self._save_top_k_checkpoint(trainer, monitor_candidates)
        # Mode 2: save monitor=None checkpoints
        self._save_none_monitor_checkpoint(trainer, monitor_candidates)
        # Mode 3: save last checkpoints
        self._save_last_checkpoint(trainer, monitor_candidates)

    def _should_skip_saving_checkpoint(self, trainer) -> bool:
        from pytorch_lightning.trainer.states import TrainerState
        return (
            trainer.fast_dev_run  # disable checkpointing with fast_dev_run
            or trainer.state != TrainerState.FITTING  # don't save anything during non-fit
            or trainer.sanity_checking  # don't save anything during sanity check
            or self._last_global_step_saved == trainer.global_step  # already saved at the last step
        )

    def __validate_init_configuration(self):
        if self.save_top_k is not None and self.save_top_k < -1:
            raise MisconfigurationException(f'Invalid value for save_top_k={self.save_top_k}. Must be None or >= -1')
        if self._every_n_train_steps < 0:
            raise MisconfigurationException(
                f'Invalid value for every_n_train_steps={self._every_n_train_steps}. Must be >= 0'
            )
        if self._every_n_val_epochs < 0:
            raise MisconfigurationException(
                f'Invalid value for every_n_val_epochs={self._every_n_val_epochs}. Must be >= 0'
            )
        if self._every_n_train_steps > 0 and self._every_n_val_epochs > 0:
            raise MisconfigurationException(
                f'Invalid values for every_n_train_steps={self._every_n_train_steps}'
                ' and every_n_val_epochs={self._every_n_val_epochs}.'
                ' Both cannot be enabled at the same time.'
            )
        if self.monitor is None:
            # None: save last epoch, -1: save all epochs, 0: nothing is saved
            if self.save_top_k not in (None, -1, 0):
                raise MisconfigurationException(
                    f'ModelCheckpoint(save_top_k={self.save_top_k}, monitor=None) is not a valid'
                    ' configuration. No quantity for top_k to track.'
                )
            if self.save_last:
                rank_zero_warn(
                    'ModelCheckpoint(save_last=True, save_top_k=None, monitor=None) is a redundant configuration.'
                    ' You can save the last checkpoint with ModelCheckpoint(save_top_k=None, monitor=None).'
                )
            if self.save_top_k == -1 and self.save_last:
                rank_zero_info(
                    'ModelCheckpoint(save_last=True, save_top_k=-1, monitor=None)'
                    ' will duplicate the last checkpoint saved.'
                )

    def __init_ckpt_dir(self, dirpath, filename, save_top_k):

        self._fs = get_filesystem(str(dirpath) if dirpath else '')

        if (
            save_top_k is not None and save_top_k > 0 and dirpath is not None and self._fs.isdir(dirpath)
            and len(self._fs.ls(dirpath)) > 0
        ):
            rank_zero_warn(f"Checkpoint directory {dirpath} exists and is not empty.")

        if dirpath and self._fs.protocol == 'file':
            dirpath = os.path.realpath(dirpath)

        self.dirpath: Union[str, None] = dirpath or None
        self.filename = filename or None

    def __init_monitor_mode(self, monitor, mode):
        torch_inf = torch.tensor(np.Inf)
        mode_dict = {
            "min": (torch_inf, "min"),
            "max": (-torch_inf, "max"),
        }

        if mode not in mode_dict:
            raise MisconfigurationException(f"`mode` can be {', '.join(mode_dict.keys())} but got {mode}")

        self.kth_value, self.mode = mode_dict[mode]

    def __init_triggers(
        self, every_n_train_steps: Optional[int], every_n_val_epochs: Optional[int], period: Optional[int]
    ) -> None:

        # Default to running once after each validation epoch if neither
        # every_n_train_steps nor every_n_val_epochs is set
        if every_n_train_steps is None and every_n_val_epochs is None:
            self._every_n_val_epochs = 1
            self._every_n_train_steps = 0
            log.debug("Both every_n_train_steps and every_n_val_epochs are not set. Setting every_n_val_epochs=1")
        else:
            self._every_n_val_epochs = every_n_val_epochs or 0
            self._every_n_train_steps = every_n_train_steps or 0

        # period takes precedence over every_n_val_epochs for backwards compatibility
        if period is not None:
            rank_zero_deprecation(
                'Argument `period` in `ModelCheckpoint` is deprecated in v1.3 and will be removed in v1.5.'
                ' Please use `every_n_val_epochs` instead.'
            )
            self._every_n_val_epochs = period

        self._period = self._every_n_val_epochs

    @property
    def period(self) -> Optional[int]:
        rank_zero_deprecation(
            'Property `period` in `ModelCheckpoint` is deprecated in v1.3 and will be removed in v1.5.'
            ' Please use `every_n_val_epochs` instead.'
        )
        return self._period

    @period.setter
    def period(self, value: Optional[int]) -> None:
        rank_zero_deprecation(
            'Property `period` in `ModelCheckpoint` is deprecated in v1.3 and will be removed in v1.5.'
            ' Please use `every_n_val_epochs` instead.'
        )
        self._period = value

    @rank_zero_only
    def _del_model(self, filepath: str):
        if self._fs.exists(filepath):
            self._fs.rm(filepath)
            log.debug(f"Removed checkpoint: {filepath}")

    def _save_model(self, trainer, filepath: str):
        if trainer.training_type_plugin.rpc_enabled:
            # RPCPlugin manages saving all model states
            # TODO: the rpc plugin should wrap trainer.save_checkpoint
            # instead of us having to do it here manually
            trainer.training_type_plugin.rpc_save_model(trainer, self._do_save, filepath)
        else:
            self._do_save(trainer, filepath)

    def _do_save(self, trainer, filepath: str):
        # in debugging, track when we save checkpoints
        trainer.dev_debugger.track_checkpointing_history(filepath)

        # make paths
        if trainer.is_global_zero:
            self._fs.makedirs(os.path.dirname(filepath), exist_ok=True)

        # delegate the saving to the trainer
        if self.save_function is not None:
            self.save_function(filepath, self.save_weights_only)
        else:
            raise ValueError(".save_function() not set")

    def check_monitor_top_k(self, trainer, current: Optional[torch.Tensor] = None) -> bool:
        if current is None:
            return False

        if self.save_top_k == -1:
            return True

        less_than_k_models = len(self.best_k_models) < self.save_top_k
        if less_than_k_models:
            return True

        if not isinstance(current, torch.Tensor):
            rank_zero_warn(
                f"{current} is supposed to be a `torch.Tensor`. Saving checkpoint may not work correctly."
                f" HINT: check the value of {self.monitor} in your validation loop",
                RuntimeWarning,
            )
            current = torch.tensor(current)

        monitor_op = {"min": torch.lt, "max": torch.gt}[self.mode]
        should_update_best_and_save = monitor_op(current, self.best_k_models[self.kth_best_model_path])

        # If using multiple devices, make sure all processes are unanimous on the decision.
        should_update_best_and_save = trainer.training_type_plugin.reduce_boolean_decision(should_update_best_and_save)

        return should_update_best_and_save

    @classmethod
    def _format_checkpoint_name(
        cls,
        filename: Optional[str],
        epoch: int,
        step: int,
        metrics: Dict[str, Any],
        prefix: str = "",
        auto_insert_metric_name: bool = True
    ) -> str:
        if not filename:
            # filename is not set, use default name
            filename = "{epoch}" + cls.CHECKPOINT_JOIN_CHAR + "{step}"

        # check and parse user passed keys in the string
        groups = re.findall(r"(\{.*?)[:\}]", filename)
        if len(groups) >= 0:
            metrics.update({"epoch": epoch, 'step': step})
            for group in groups:
                name = group[1:]

                if auto_insert_metric_name:
                    filename = filename.replace(group, name + "={" + name)

                if name not in metrics:
                    metrics[name] = 0
            filename = filename.format(**metrics)

        if prefix:
            filename = cls.CHECKPOINT_JOIN_CHAR.join([prefix, filename])

        return filename

    def format_checkpoint_name(self, epoch: int, step: int, metrics: Dict[str, Any], ver: Optional[int] = None) -> str:
        """Generate a filename according to the defined template.

        Example::

            >>> tmpdir = os.path.dirname(__file__)
            >>> ckpt = ModelCheckpoint(dirpath=tmpdir, filename='{epoch}')
            >>> os.path.basename(ckpt.format_checkpoint_name(0, 1, metrics={}))
            'epoch=0.ckpt'
            >>> ckpt = ModelCheckpoint(dirpath=tmpdir, filename='{epoch:03d}')
            >>> os.path.basename(ckpt.format_checkpoint_name(5, 2, metrics={}))
            'epoch=005.ckpt'
            >>> ckpt = ModelCheckpoint(dirpath=tmpdir, filename='{epoch}-{val_loss:.2f}')
            >>> os.path.basename(ckpt.format_checkpoint_name(2, 3, metrics=dict(val_loss=0.123456)))
            'epoch=2-val_loss=0.12.ckpt'
            >>> ckpt = ModelCheckpoint(dirpath=tmpdir,
            ... filename='epoch={epoch}-validation_loss={val_loss:.2f}',
            ... auto_insert_metric_name=False)
            >>> os.path.basename(ckpt.format_checkpoint_name(2, 3, metrics=dict(val_loss=0.123456)))
            'epoch=2-validation_loss=0.12.ckpt'
            >>> ckpt = ModelCheckpoint(dirpath=tmpdir, filename='{missing:d}')
            >>> os.path.basename(ckpt.format_checkpoint_name(0, 4, metrics={}))
            'missing=0.ckpt'
            >>> ckpt = ModelCheckpoint(filename='{step}')
            >>> os.path.basename(ckpt.format_checkpoint_name(0, 0, {}))
            'step=0.ckpt'

        """
        filename = self._format_checkpoint_name(
            self.filename, epoch, step, metrics, auto_insert_metric_name=self.auto_insert_metric_name
        )

        if ver is not None:
            filename = self.CHECKPOINT_JOIN_CHAR.join((filename, f"v{ver}"))

        ckpt_name = f"{filename}{self.FILE_EXTENSION}"
        return os.path.join(self.dirpath, ckpt_name) if self.dirpath else ckpt_name

    def __resolve_ckpt_dir(self, trainer):
        """
        Determines model checkpoint save directory at runtime. References attributes from the
        trainer's logger to determine where to save checkpoints.
        The base path for saving weights is set in this priority:

        1.  Checkpoint callback's path (if passed in)
        2.  The default_root_dir from trainer if trainer has no logger
        3.  The weights_save_path from trainer, if user provides it
        4.  User provided weights_saved_path

        The base path gets extended with logger name and version (if these are available)
        and subfolder "checkpoints".
        """
        # Todo: required argument `pl_module` is not used
        if self.dirpath is not None:
            return  # short circuit

        if trainer.logger is not None:
            if trainer.weights_save_path != trainer.default_root_dir:
                # the user has changed weights_save_path, it overrides anything
                save_dir = trainer.weights_save_path
            else:
                save_dir = trainer.logger.save_dir or trainer.default_root_dir

            version = (
                trainer.logger.version
                if isinstance(trainer.logger.version, str) else f"version_{trainer.logger.version}"
            )

            version, name = trainer.training_type_plugin.broadcast((version, trainer.logger.name))

            ckpt_path = os.path.join(save_dir, str(name), version, "checkpoints")
        else:
            ckpt_path = os.path.join(trainer.weights_save_path, "checkpoints")

        self.dirpath = ckpt_path

        if not trainer.fast_dev_run and trainer.is_global_zero:
            self._fs.makedirs(self.dirpath, exist_ok=True)

    def _add_backward_monitor_support(self, trainer):
        metrics = trainer.logger_connector.callback_metrics
        deprecation_warning = False

        if self.monitor is None and 'val_loss' in metrics:
            self.monitor = 'val_loss'
            deprecation_warning = True

        if self.save_top_k is None and self.monitor is not None:
            # TODO: Remove `Optional` from `save_top_k` when this is deleted in v1.4
            self.save_top_k = 1

        if deprecation_warning:
            warning_cache.warn(
                "Relying on `self.log('val_loss', ...)` to set the ModelCheckpoint monitor is deprecated in v1.2"
                " and will be removed in v1.4. Please, create your own `mc = ModelCheckpoint(monitor='your_monitor')`"
                " and use it as `Trainer(callbacks=[mc])`.", DeprecationWarning
            )

    def _validate_monitor_key(self, trainer):
        metrics = trainer.logger_connector.callback_metrics

        # validate metric
        if self.monitor is not None and not self._is_valid_monitor_key(metrics):
            m = (
                f"ModelCheckpoint(monitor='{self.monitor}') not found in the returned metrics:"
                f" {list(metrics.keys())}. "
                f"HINT: Did you call self.log('{self.monitor}', tensor) in the LightningModule?"
            )
            raise MisconfigurationException(m)

    def _get_metric_interpolated_filepath_name(
        self,
        monitor_candidates: Dict[str, Any],
        epoch: int,
        step: int,
        trainer,
        del_filepath: Optional[str] = None,
    ) -> str:
        filepath = self.format_checkpoint_name(epoch, step, monitor_candidates)

        version_cnt = self.STARTING_VERSION
        while self.file_exists(filepath, trainer) and filepath != del_filepath:
            filepath = self.format_checkpoint_name(epoch, step, monitor_candidates, ver=version_cnt)
            version_cnt += 1

        return filepath

    def _monitor_candidates(self, trainer):
        monitor_candidates = deepcopy(trainer.logger_connector.callback_metrics)
        monitor_candidates.update(step=trainer.global_step, epoch=trainer.current_epoch)
        return monitor_candidates

    def _save_last_checkpoint(self, trainer, monitor_candidates: Dict[str, Any]):
        if not self.save_last:
            return

        filepath = self._format_checkpoint_name(
            self.CHECKPOINT_NAME_LAST,
            trainer.current_epoch,
            trainer.global_step,
            monitor_candidates,
        )
        filepath = os.path.join(self.dirpath, f"{filepath}{self.FILE_EXTENSION}")

        self._save_model(trainer, filepath)

        if self.last_model_path and self.last_model_path != filepath and trainer.is_global_zero:
            self._del_model(self.last_model_path)

        self.last_model_path = filepath

    def _save_top_k_checkpoint(self, trainer, monitor_candidates: Dict[str, Any]):
        if self.monitor is None or self.save_top_k == 0:
            return

        current = monitor_candidates.get(self.monitor)
        epoch = monitor_candidates.get("epoch")
        step = monitor_candidates.get("step")

        if self.check_monitor_top_k(trainer, current):
            self._update_best_and_save(current, epoch, step, trainer, monitor_candidates)
        elif self.verbose:
            rank_zero_info(f"Epoch {epoch:d}, step {step:d}: {self.monitor} was not in top {self.save_top_k}")

    def _save_none_monitor_checkpoint(self, trainer, monitor_candidates: Dict[str, Any]):
        if self.monitor is not None or self.save_top_k == 0:
            return

        filepath = self._get_metric_interpolated_filepath_name(
            monitor_candidates,
            trainer.current_epoch,
            trainer.global_step,
            trainer,
        )
        self._save_model(trainer, filepath)

        if (
            self.save_top_k is None and self.best_model_path and self.best_model_path != filepath
            and trainer.is_global_zero
        ):
            self._del_model(self.best_model_path)

        self.best_model_path = filepath

    def _is_valid_monitor_key(self, metrics):
        return self.monitor in metrics or len(metrics) == 0

    def _update_best_and_save(
        self, current: torch.Tensor, epoch: int, step: int, trainer, monitor_candidates: Dict[str, Any]
    ):
        k = len(self.best_k_models) + 1 if self.save_top_k == -1 else self.save_top_k

        del_filepath = None
        if len(self.best_k_models) == k and k > 0:
            del_filepath = self.kth_best_model_path
            self.best_k_models.pop(del_filepath)

        # do not save nan, replace with +/- inf
        if isinstance(current, torch.Tensor) and torch.isnan(current):
            current = torch.tensor(float('inf' if self.mode == "min" else '-inf'))

        filepath = self._get_metric_interpolated_filepath_name(monitor_candidates, epoch, step, trainer, del_filepath)

        # save the current score
        self.current_score = current
        self.best_k_models[filepath] = current

        if len(self.best_k_models) == k:
            # monitor dict has reached k elements
            _op = max if self.mode == "min" else min
            self.kth_best_model_path = _op(self.best_k_models, key=self.best_k_models.get)
            self.kth_value = self.best_k_models[self.kth_best_model_path]

        _op = min if self.mode == "min" else max
        self.best_model_path = _op(self.best_k_models, key=self.best_k_models.get)
        self.best_model_score = self.best_k_models[self.best_model_path]

        if self.verbose:
            rank_zero_info(
                f"Epoch {epoch:d}, global step {step:d}: {self.monitor} reached {current:0.5f}"
                f' (best {self.best_model_score:0.5f}), saving model to "{filepath}" as top {k}'
            )
        self._save_model(trainer, filepath)

        if del_filepath is not None and filepath != del_filepath:
            self._del_model(del_filepath)

    def to_yaml(self, filepath: Optional[Union[str, Path]] = None):
        """
        Saves the `best_k_models` dict containing the checkpoint
        paths with the corresponding scores to a YAML file.
        """
        best_k = {k: v.item() for k, v in self.best_k_models.items()}
        if filepath is None:
            filepath = os.path.join(self.dirpath, "best_k_models.yaml")
        with self._fs.open(filepath, "w") as fp:
            yaml.dump(best_k, fp)

    def file_exists(self, filepath: Union[str, Path], trainer) -> bool:
        """
        Checks if a file exists on rank 0 and broadcasts the result to all other ranks, preventing
        the internal state to diverge between ranks.
        """
        exists = self._fs.exists(filepath)
        return trainer.training_type_plugin.broadcast(exists)
