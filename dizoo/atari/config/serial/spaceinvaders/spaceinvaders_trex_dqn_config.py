from copy import deepcopy
from easydict import EasyDict

spaceinvaders_trex_dqn_config = dict(
    exp_name='spaceinvaders_trex_dqn_seed0',
    env=dict(
        collector_env_num=8,
        evaluator_env_num=8,
        n_evaluator_episode=8,
        stop_value=10000000000,
        env_id='SpaceInvadersNoFrameskip-v4',
        frame_stack=4,
        manager=dict(shared_memory=False, )
    ),
    reward_model=dict(
        type='trex',
        algo_for_model='dqn',
        env_id='SpaceInvadersNoFrameskip-v4',
        min_snippet_length=50,
        max_snippet_length=100,
        checkpoint_min=10000,
        checkpoint_max=90000,
        checkpoint_step=10000,
        num_snippets=100000,
        learning_rate=1e-5,
        update_per_collect=1,
        # path to expert models that generate demonstration data
        # Users should add their own model path here. Model path should lead to an exp_name.
        # Absolute path is recommended.
        # In DI-engine, it is ``exp_name``.
        # For example, if you want to use dqn to generate demos, you can use ``spaceinvaders_dqn``
        expert_model_path='expert_model_path_placeholder',
        # path to save reward model
        # Users should add their own model path here.
        # Absolute path is recommended.
        # For example, if you use ``spaceinvaders_drex``, then the reward model will be saved in this directory.
        reward_model_path='reward_model_path_placeholder + ./spaceinvaders.params',
        # path to save generated observations.
        # Users should add their own model path here.
        # Absolute path is recommended.
        # For example, if you use ``spaceinvaders_drex``, then all the generated data will be saved in this directory.
        offline_data_path='offline_data_path_placeholder',
    ),
    policy=dict(
        cuda=True,
        priority=False,
        model=dict(
            obs_shape=[4, 84, 84],
            action_shape=6,
            encoder_hidden_size_list=[128, 128, 512],
        ),
        nstep=1,
        discount_factor=0.99,
        learn=dict(
            update_per_collect=10,
            batch_size=32,
            learning_rate=0.0001,
            target_update_freq=500,
        ),
        collect=dict(n_sample=100, ),
        eval=dict(evaluator=dict(eval_freq=100, )),
        other=dict(
            eps=dict(
                type='exp',
                start=1.,
                end=0.05,
                decay=1000000,
            ),
            replay_buffer=dict(replay_buffer_size=400000, ),
        ),
    ),
)
spaceinvaders_trex_dqn_config = EasyDict(spaceinvaders_trex_dqn_config)
main_config = spaceinvaders_trex_dqn_config
spaceinvaders_trex_dqn_create_config = dict(
    env=dict(
        type='atari',
        import_names=['dizoo.atari.envs.atari_env'],
    ),
    env_manager=dict(type='subprocess'),
    policy=dict(type='dqn'),
)
spaceinvaders_trex_dqn_create_config = EasyDict(spaceinvaders_trex_dqn_create_config)
create_config = spaceinvaders_trex_dqn_create_config

if __name__ == '__main__':
    from ding.entry import trex_collecting_data, serial_pipeline_reward_model_trex

    args = EasyDict(dict(cfg=[main_config, create_config], seed=0, device='cuda'))
    trex_collecting_data(args)
    serial_pipeline_reward_model_trex([main_config, create_config], seed=0)
