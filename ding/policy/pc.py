import math
import torch
import torch.nn as nn
import copy
from torch.optim import Adam, SGD, AdamW
from torch.optim.lr_scheduler import LambdaLR
import logging
from typing import List, Dict, Any, Tuple, Union, Optional
from collections import namedtuple
from easydict import EasyDict
from ding.policy import Policy
from ding.model import model_wrap
from ding.torch_utils import to_device, to_list
from ding.utils import EasyTimer
from ding.utils.data import default_collate, default_decollate
from ding.rl_utils import get_nstep_return_data, get_train_sample
from ding.utils import POLICY_REGISTRY
from ding.torch_utils.loss.cross_entropy_loss import LabelSmoothCELoss


def print_obs(obs):
    print('Wall')
    print(obs[0, :, :, 0])
    print('Goal')
    print(obs[0, :, :, 1])
    print('Obs')
    print(obs[0, :, :, 2])


def get_vi_sequence(target, observation, map):
    """Returns [L, W, W] optimal actions."""
    start_x, start_y = observation
    target_location = target
    nav_map = map
    current_points = [target_location]
    chosen_actions = {target_location: 0}
    visited_points = {target_location: True}
    vi_sequence = []
    vi_map = 4 * torch.ones_like(map)

    found_start = False
    while current_points and not found_start:
        next_points = []
        for point_x, point_y in current_points:
            for (action, (next_point_x, next_point_y)) in [(0, (point_x - 1, point_y)), (1, (point_x, point_y - 1)),
                                                           (2, (point_x + 1, point_y)), (3, (point_x, point_y + 1))]:

                if (next_point_x, next_point_y) in visited_points:
                    continue

                if not (0 <= next_point_x < len(nav_map) and 0 <= next_point_y < len(nav_map[next_point_x])):
                    continue

                if nav_map[next_point_x][next_point_y] == 'x':
                    continue

                next_points.append((next_point_x, next_point_y))
                visited_points[(next_point_x, next_point_y)] = True
                chosen_actions[(next_point_x, next_point_y)] = action
                vi_map[next_point_x, next_point_y] = action

                if next_point_x == start_x and next_point_y == start_y:
                    found_start = True
        vi_sequence.append(vi_map.copy())
        current_points = next_points

    return vi_sequence


@POLICY_REGISTRY.register('pc')
class ProcedureCloningPolicy(Policy):

    def default_model(self) -> Tuple[str, List[str]]:
        return 'pc', ['ding.model.template.pc']

    config = dict(
        type='pc',
        cuda=False,
        on_policy=False,
        continuous=False,
        max_bfs_steps=100,
        learn=dict(
            multi_gpu=False,
            update_per_collect=1,
            batch_size=32,
            learning_rate=1e-5,
            lr_decay=False,
            decay_epoch=30,
            decay_rate=0.1,
            warmup_lr=1e-4,
            warmup_epoch=3,
            optimizer='SGD',
            momentum=0.9,
            weight_decay=1e-4,
            ce_label_smooth=False,
            show_accuracy=False,
            tanh_mask=False,  # if actions always converge to 1 or -1, use this.
        ),
        collect=dict(
            unroll_len=1,
            noise=False,
            noise_sigma=0.2,
            noise_range=dict(
                min=-0.5,
                max=0.5,
            ),
        ),
        eval=dict(),
        other=dict(replay_buffer=dict(replay_buffer_size=10000, )),
    )

    def _init_learn(self):
        assert self._cfg.learn.optimizer in ['SGD', 'Adam']
        if self._cfg.learn.optimizer == 'SGD':
            self._optimizer = SGD(
                self._model.parameters(),
                lr=self._cfg.learn.learning_rate,
                weight_decay=self._cfg.learn.weight_decay,
                momentum=self._cfg.learn.momentum
            )
        elif self._cfg.learn.optimizer == 'Adam':
            if self._cfg.learn.weight_decay is None:
                self._optimizer = Adam(
                    self._model.parameters(),
                    lr=self._cfg.learn.learning_rate,
                )
            else:
                self._optimizer = AdamW(
                    self._model.parameters(),
                    lr=self._cfg.learn.learning_rate,
                    weight_decay=self._cfg.learn.weight_decay
                )
        if self._cfg.learn.lr_decay:

            def lr_scheduler_fn(epoch):
                if epoch <= self._cfg.learn.warmup_epoch:
                    return self._cfg.learn.warmup_lr / self._cfg.learn.learning_rate
                else:
                    ratio = (epoch - self._cfg.learn.warmup_epoch) // self._cfg.learn.decay_epoch
                    return math.pow(self._cfg.learn.decay_rate, ratio)

            self._lr_scheduler = LambdaLR(self._optimizer, lr_scheduler_fn)
        self._timer = EasyTimer(cuda=True)
        self._learn_model = model_wrap(self._model, 'base')
        self._learn_model.reset()
        self._max_bfs_steps = self._cfg.max_bfs_steps
        self._maze_size = self._cfg.maze_size
        self._num_actions = self._cfg.num_actions

        self._loss = nn.CrossEntropyLoss()

    def process_states(self, observations, maze_maps):
        """Returns [B, W, W, 3] binary values. Channels are (wall; goal; obs)"""
        loc = torch.nn.functional.one_hot(
            (observations[:, 0] * self._maze_size + observations[:, 1]).long(),
            self._maze_size * self._maze_size,
        ).long()
        loc = torch.reshape(loc, [observations.shape[0], self._maze_size, self._maze_size])
        states = torch.cat([maze_maps, loc], dim=-1).long()
        # if self._augment and training:
        #     states = self._augment_layers(states)
        return states

    def _forward_learn(self, data):
        if self._cuda:
            collated_data = to_device(data, self._device)
        else:
            collated_data = data
        observations, bfs_input_maps, bfs_output_maps = collated_data['obs'], collated_data['bfs_in'].long(), \
                                                        collated_data['bfs_out'].long()
        states = observations
        bfs_input_onehot = torch.nn.functional.one_hot(bfs_input_maps, self._num_actions + 1).float()
        bfs_states = torch.cat([states, bfs_input_onehot], dim=-1)
        logits = self._model(bfs_states)['logit']
        # print('##############################')
        # print(torch.argmax(logits[0], dim=-1))
        # print(bfs_output_maps[0])
        # print('##############################')
        my_preds = torch.argmax(logits, dim=-1)
        # for ii in range(bfs_input_maps.shape[0]):
        #     if torch.sum(bfs_input_maps[ii]) == 4 * 16 * 16:
        #         print('####################################################')
        #         print(my_preds[ii])
        logits = logits.flatten(0, -2)
        labels = bfs_output_maps.flatten(0, -1)

        loss = self._loss(logits, labels)
        preds = torch.argmax(logits, dim=-1)
        acc = torch.sum((preds == labels)) / preds.shape[0]
        non_4_ratio = 1 - (torch.sum((preds == 4)) / preds.shape[0])

        self._optimizer.zero_grad()
        loss.backward()
        self._optimizer.step()
        pred_loss = loss.item()

        cur_lr = [param_group['lr'] for param_group in self._optimizer.param_groups]
        cur_lr = sum(cur_lr) / len(cur_lr)
        return {
            'cur_lr': cur_lr,
            'total_loss': pred_loss,
            'acc': acc,
            'non_4_ratio': non_4_ratio
        }

    def _monitor_vars_learn(self):
        return ['cur_lr', 'total_loss', 'acc', 'non_4_ratio']

    def _init_eval(self):
        self._eval_model = model_wrap(self._model, wrapper_name='base')
        self._eval_model.reset()

    def _forward_eval(self, data):
        if self._cuda:
            data = to_device(data, self._device)
        max_len = self._max_bfs_steps
        data_id = list(data.keys())
        output = {}

        for ii in data_id:
            states = data[ii].unsqueeze(0)
            bfs_input_maps = self._num_actions * torch.ones([1, self._maze_size, self._maze_size]).long()
            if self._cuda:
                bfs_input_maps = to_device(bfs_input_maps, self._device)
            xy = torch.where(states[:, :, :, -1] == 1)
            observation = (xy[1][0].item(), xy[2][0].item())

            wall = copy.deepcopy(states[:, :, :, 0])

            xy = torch.where(states[:, :, :, -2] == 1)
            goal = (xy[1][0].item(), xy[2][0].item())

            i = 0
            print_obs(states)
            print(observation)
            print(goal)
            seq = get_vi_sequence(goal, observation, wall)
            print(seq[0])
            print(seq[1])
            assert False
            while bfs_input_maps[0, observation[0], observation[1]].item() == self._num_actions and i < max_len:
                print(bfs_input_maps)
                bfs_input_onehot = torch.nn.functional.one_hot(bfs_input_maps, self._num_actions + 1).long()
                bfs_states = torch.cat([states, bfs_input_onehot], dim=-1)
                logits = self._model(bfs_states)['logit']
                bfs_input_maps = torch.argmax(logits, dim=-1)
                i += 1
            print(i)
            output[ii] = bfs_input_maps[0, observation[0], observation[1]]
            if self._cuda:
                output[ii] = {'action': to_device(output[ii], 'cpu'), 'info': {}}
            if output[ii]['action'].item() == self._num_actions:
                output[ii]['action'] = torch.randint(low=0, high=self._num_actions, size=[1])[0]
        assert False
        return output

    def _init_collect(self) -> None:
        r"""
        Overview:
            Collect mode init method. Called by ``self.__init__``.
            Init traj and unroll length, collect model.
            Enable the eps_greedy_sample
        """
        self._collect_model = model_wrap(self._model, wrapper_name='base')
        self._collect_model.reset()

    def _forward_collect(self, data: Dict[int, Any], **kwargs) -> Dict[int, Any]:
        r"""
        Overview:
            Forward function for collect mode with eps_greedy
        Arguments:
            - data (:obj:`dict`): Dict type data, including at least ['obs'].
        Returns:
            - data (:obj:`dict`): The collected data
        """
        raise NotImplementedError

    def _process_transition(self, obs: Any, model_output: dict, timestep: namedtuple) -> dict:
        r"""
        Overview:
            Generate dict type transition data from inputs.
        Arguments:
            - obs (:obj:`Any`): Env observation
            - model_output (:obj:`dict`): Output of collect model, including at least ['action']
            - timestep (:obj:`namedtuple`): Output after env step, including at least ['obs', 'reward', 'done'] \
                (here 'obs' indicates obs after env step).
        Returns:
            - transition (:obj:`dict`): Dict type transition data.
        """
        transition = {
            'obs': obs,
            'next_obs': timestep.obs,
            'action': model_output['action'],
            'reward': timestep.reward,
            'done': timestep.done,
        }
        return EasyDict(transition)

    def _get_train_sample(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Overview:
            For a given trajectory(transitions, a list of transition) data, process it into a list of sample that \
            can be used for training directly. A train sample can be a processed transition(DQN with nstep TD) \
            or some continuous transitions(DRQN).
        Arguments:
            - data (:obj:`List[Dict[str, Any]`): The trajectory data(a list of transition), each element is the same \
                format as the return value of ``self._process_transition`` method.
        Returns:
            - samples (:obj:`dict`): The list of training samples.

        .. note::
            We will vectorize ``process_transition`` and ``get_train_sample`` method in the following release version. \
            And the user can customize the this data processing procecure by overriding this two methods and collector \
            itself.
        """
        data = get_nstep_return_data(data, 1, 1)
        return get_train_sample(data, self._unroll_len)
