import gym
import torch
from easydict import EasyDict

from ding.reward_model import LlamaRewardModel
from .utils import OnlyPromptDataset, concat_context_and_response, get_tokenizer, pad_sequences


class ChatEnv(gym.Env):
    def __init__(
            self,
            batch_size,
            reward_model_path,
            tokenizer_path,
            data_path,
            maxlen_prompt,
            maxlen_res,
    ):
        self.batch_size = batch_size
        self.tokenizer = get_tokenizer(tokenizer_path)
        self.rm = LlamaRewardModel.from_pretrained(reward_model_path, tokenizer=self.tokenizer, opt=None)
        self.action_space = None

        self.dataset = OnlyPromptDataset(
            data_path=data_path,
            tokenizer=self.tokenizer,
            batch_size=batch_size,
            maxlen_prompt=maxlen_prompt,
            maxlen_res=maxlen_res,
            mode='train',
        )
        self.generator = self.dataset.final_generator()
        self.last_batch = None

    def reset(self):
        self.last_batch = next(self.generator)
        return self.last_batch

    def step(self, action):
        """
        For each step, this env will return a batch of prompts. These prompts a vectorized by using tokenizer, and are \
        padded into the same length.
        """
        output_mask, output_vec = concat_context_and_response(self.tokenizer, self.last_batch['text_vec'].tolist(), action)
        rm_input = torch.tensor(pad_sequences(output_vec, self.tokenizer.pad_token_id, padding='left'), dtype=torch.long)
        output_mask = pad_sequences(output_mask, self.tokenizer.pad_token_id, padding='left')
        with torch.no_grad():
            rew, *_ = self.rm(rm_input)

        self.last_batch = next(self.generator)
        if self.last_batch is None:
            self.generator = self.dataset.final_generator()
            self.last_batch = next(self.generator)

        return output_mask, output_vec, rew
