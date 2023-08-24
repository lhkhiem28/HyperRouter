import pdb
import argparse
import time
import math
import os, sys
import itertools

import numpy as np

import torch
import torch.nn as nn
import torch.optim as optim

from data_utils import get_lm_corpus
from mem_transformer_downstream import MemTransformerLM
from utils.exp_utils import create_exp_dir
from utils.data_parallel import BalancedDataParallel
from fmoe.gates.base_gate import BaseGate

from latest_utils import *

import warnings 
warnings.filterwarnings(action= 'ignore')

parser = argparse.ArgumentParser(description='PyTorch Transformer Language Model')

parser.add_argument('--pretrained_weight', default=None, type=str)

parser.add_argument('--data', type=str, default='../data/wikitext-103',
                    help='location of the data corpus')
parser.add_argument('--dataset', type=str, default='wt103',
                    choices=['wt103', 'lm1b', 'enwik8', 'text8', 'csqa', 'sst2', 'imdb', 'sst5', 'banking77'],
                    help='dataset name')
parser.add_argument('--n_layer', type=int, default=12,
                    help='number of total layers')
parser.add_argument('--n_head', type=int, default=10,
                    help='number of heads')
parser.add_argument('--d_head', type=int, default=50,
                    help='head dimension')
parser.add_argument('--d_embed', type=int, default=-1,
                    help='embedding dimension')
parser.add_argument('--d_model', type=int, default=500,
                    help='model dimension')
parser.add_argument('--d_inner', type=int, default=1000,
                    help='inner dimension in FF')
parser.add_argument('--dropout', type=float, default=0.0,
                    help='global dropout rate')
parser.add_argument('--dropatt', type=float, default=0.0,
                    help='attention probability dropout rate')
parser.add_argument('--init', default='normal', type=str,
                    help='parameter initializer to use.')
parser.add_argument('--emb_init', default='normal', type=str,
                    help='parameter initializer to use.')
parser.add_argument('--init_range', type=float, default=0.1,
                    help='parameters initialized by U(-init_range, init_range)')
parser.add_argument('--emb_init_range', type=float, default=0.01,
                    help='parameters initialized by U(-init_range, init_range)')
parser.add_argument('--init_std', type=float, default=0.02,
                    help='parameters initialized by N(0, init_std)')
parser.add_argument('--proj_init_std', type=float, default=0.01,
                    help='parameters initialized by N(0, init_std)')
parser.add_argument('--optim', default='adam', type=str,
                    choices=['adam', 'sgd', 'adagrad'],
                    help='optimizer to use.')
parser.add_argument('--lr', type=float, default=0.00025,
                    help='initial learning rate (0.00025|5 for adam|sgd)')
parser.add_argument('--mom', type=float, default=0.0,
                    help='momentum for sgd')
parser.add_argument('--scheduler', default='cosine', type=str,
                    choices=['cosine', 'inv_sqrt', 'dev_perf', 'constant'],
                    help='lr scheduler to use.')
parser.add_argument('--warmup_step', type=int, default=0,
                    help='upper epoch limit')
parser.add_argument('--decay_rate', type=float, default=0.5,
                    help='decay factor when ReduceLROnPlateau is used')
parser.add_argument('--lr_min', type=float, default=0.0,
                    help='minimum learning rate during annealing')
parser.add_argument('--clip', type=float, default=0.25,
                    help='gradient clipping')
parser.add_argument('--clip_nonemb', action='store_true',
                    help='only clip the gradient of non-embedding params')
parser.add_argument('--max_step', type=int, default=100000,
                    help='upper epoch limit')
parser.add_argument('--batch_size', type=int, default=60,
                    help='batch size')
parser.add_argument('--batch_chunk', type=int, default=1,
                    help='split batch into chunks to save memory')
parser.add_argument('--tgt_len', type=int, default=70,
                    help='number of tokens to predict')
parser.add_argument('--eval_tgt_len', type=int, default=50,
                    help='number of tokens to predict for evaluation')
parser.add_argument('--ext_len', type=int, default=0,
                    help='length of the extended context')
parser.add_argument('--mem_len', type=int, default=0,
                    help='length of the retained previous heads')
parser.add_argument('--not_tied', action='store_true',
                    help='do not tie the word embedding and softmax weights')
parser.add_argument('--seed', type=int, default=1111,
                    help='random seed')
parser.add_argument('--cuda', action='store_true',
                    help='use CUDA')
parser.add_argument('--adaptive', action='store_true',
                    help='use adaptive softmax')
parser.add_argument('--div_val', type=int, default=1,
                    help='divident value for adapative input and softmax')
parser.add_argument('--pre_lnorm', action='store_true',
                    help='apply LayerNorm to the input instead of the output')
parser.add_argument('--varlen', action='store_true',
                    help='use variable length')
parser.add_argument('--multi_gpu', action='store_true',
                    help='use multiple GPU')
parser.add_argument('--log-interval', type=int, default=200,
                    help='report interval')
parser.add_argument('--eval-interval', type=int, default=4000,
                    help='evaluation interval')
parser.add_argument('--work_dir', default='LM-TFM', type=str,
                    help='experiment directory.')
parser.add_argument('--restart', action='store_true',
                    help='restart training from the saved checkpoint')
parser.add_argument('--restart_dir', type=str, default='',
                    help='restart dir')
parser.add_argument('--debug', action='store_true',
                    help='run in debug mode (do not create exp dir)')
parser.add_argument('--same_length', action='store_true',
                    help='use the same attn length for all tokens')
parser.add_argument('--attn_type', type=int, default=0,
                    help='attention type. 0 for ours, 1 for Shaw et al,'
                    '2 for Vaswani et al, 3 for Al Rfou et al.')
parser.add_argument('--clamp_len', type=int, default=-1,
                    help='use the same pos embeddings after clamp_len')
parser.add_argument('--eta_min', type=float, default=0.0,
                    help='min learning rate for cosine scheduler')
parser.add_argument('--gpu0_bsz', type=int, default=-1,
                    help='batch size on gpu 0')
parser.add_argument('--max_eval_steps', type=int, default=-1,
                    help='max eval steps')
parser.add_argument('--sample_softmax', type=int, default=-1,
                    help='number of samples in sampled softmax')
parser.add_argument('--patience', type=int, default=0,
                    help='patience')
parser.add_argument('--finetune_v2', action='store_true',
                    help='finetune v2')
parser.add_argument('--finetune_v3', action='store_true',
                    help='finetune v3')
parser.add_argument('--fp16', action='store_true',
                    help='Run in pseudo-fp16 mode (fp16 storage fp32 math).')
parser.add_argument('--static-loss-scale', type=float, default=1,
                    help='Static loss scale, positive power of 2 values can '
                    'improve fp16 convergence.')
parser.add_argument('--dynamic-loss-scale', action='store_true',
                    help='Use dynamic loss scaling.  If supplied, this argument'
                    ' supersedes --static-loss-scale.')
parser.add_argument('--moe', action='store_true',
                    help='replace position-wise ffn with moe position-wise ffn')
parser.add_argument('--moe-num-expert', type=int, default=64,
                    help='number of experts in MoE')

parser.add_argument('--moe-top-k', type=int, default=2,
                    help='top_k experts in hard gate of moe')

## other settings
parser.add_argument('--gate_name', type=str, default='NaiveGate',
                    help='Router Type')
parser.add_argument('--moe_index', type=str, default=None, help='MoE Index')                    
## Random Weight 
parser.add_argument('--freeze_gate', action='store_true')
parser.add_argument('--freeze_main_network', action='store_true')
parser.add_argument('--freeze_main_network_all', action='store_true')
## Gradually adjust Top-K number during training
parser.add_argument('--dynamic_moe', action='store_true',
                    help='dynamic change moe top-k')
parser.add_argument('--dynamic_moe_mode', type=str, default='linear_increase')
parser.add_argument('--dynamic_overall_steps', type=int, default=-1)
parser.add_argument('--moe-top-k-min', type=int, default=2)
parser.add_argument('--moe-top-k-max', type=int, default=16)

## Dense to Sparse
parser.add_argument('--min_temp', type=int, default=0.3)
parser.add_argument('--max_temp', type=int, default=2)
parser.add_argument('--threshold', type=int, default=0.001)
## Dense Dropout
parser.add_argument('--dense_drop', action='store_true')
parser.add_argument('--expert_drop', type=float, default=0.5)
parser.add_argument('--num_expert', type=int, default=64)
## SWAD/SWA
parser.add_argument('--swad', action='store_true')
parser.add_argument('--swad_start', type=int, default=0)
parser.add_argument('--swad_end', type=int, default=400000)
## Dynamic Routing
parser.add_argument('--dynamic_router_start', type=int, default=-1)

args = parser.parse_args()
args.tied = not args.not_tied
assert args.moe_num_expert >= args.moe_top_k, "must have moe-num-expert >= moe-top_k"

if args.d_embed < 0:
    args.d_embed = args.d_model

assert args.ext_len >= 0, 'extended context length must be non-negative'
assert args.batch_size % args.batch_chunk == 0

args.work_dir = '{}-{}'.format(args.work_dir, args.dataset)
args.work_dir = os.path.join(args.work_dir, time.strftime('%Y%m%d-%H%M%S'))
logging = create_exp_dir(args.work_dir,
    scripts_to_save=['train.py', 'mem_transformer.py'], debug=args.debug)

# Set the random seed manually for reproducibility.
np.random.seed(args.seed)
torch.manual_seed(args.seed)
if torch.cuda.is_available():
    if not args.cuda:
        print('WARNING: You have a CUDA device, so you should probably run with --cuda')
    else:
        torch.cuda.manual_seed_all(args.seed)

# Validate `--fp16` option
if args.fp16:
    if not args.cuda:
        print('WARNING: --fp16 requires --cuda, ignoring --fp16 option')
        args.fp16 = False
    else:
        try:
            from apex.fp16_utils import FP16_Optimizer
        except:
            print('WARNING: apex not installed, ignoring --fp16 option')
            args.fp16 = False

device = torch.device('cuda' if args.cuda else 'cpu')

###############################################################################
# Load data
###############################################################################
corpus = get_lm_corpus(args.data, args.dataset)
ntokens = len(corpus.vocab)
args.n_token = ntokens

if args.dataset in ["sst2", "imdb"]:
    num_classes = 2
if args.dataset == "sst5":
    num_classes = 5
if args.dataset == "banking77":
    num_classes = 77

eval_batch_size = 10

# for CSQA
# tr_iter = corpus.get_iterator('train', args.batch_size)
# va_iter = corpus.get_iterator('valid', args.batch_size)
# te_iter = va_iter

tr_iter = corpus.get_iterator('train', args.batch_size)
va_iter = corpus.get_iterator('valid', args.batch_size)
te_iter = va_iter

# adaptive softmax / embedding
cutoffs, tie_projs = [], [False]
if args.adaptive:
    assert args.dataset in ['wt103', 'lm1b']
    if args.dataset == 'wt103':
        cutoffs = [20000, 40000, 200000]
        tie_projs += [True] * len(cutoffs)
    elif args.dataset == 'lm1b':
        cutoffs = [60000, 100000, 640000]
        tie_projs += [False] * len(cutoffs)

###############################################################################
# Build the model
###############################################################################
def init_weight(weight):
    if args.init == 'uniform':
        nn.init.uniform_(weight, -args.init_range, args.init_range)
    elif args.init == 'normal':
        nn.init.normal_(weight, 0.0, args.init_std)

def init_bias(bias):
    nn.init.constant_(bias, 0.0)

def weights_init(m):
    classname = m.__class__.__name__
    if classname.find('Linear') != -1:
        if hasattr(m, 'weight') and m.weight is not None:
            init_weight(m.weight)
        if hasattr(m, 'bias') and m.bias is not None:
            init_bias(m.bias)
    elif classname.find('AdaptiveEmbedding') != -1:
        if hasattr(m, 'emb_projs'):
            for i in range(len(m.emb_projs)):
                if m.emb_projs[i] is not None:
                    nn.init.normal_(m.emb_projs[i], 0.0, args.proj_init_std)
    elif classname.find('Embedding') != -1:
        if hasattr(m, 'weight'):
            init_weight(m.weight)
    elif classname.find('ProjectedAdaptiveLogSoftmax') != -1:
        if hasattr(m, 'cluster_weight') and m.cluster_weight is not None:
            init_weight(m.cluster_weight)
        if hasattr(m, 'cluster_bias') and m.cluster_bias is not None:
            init_bias(m.cluster_bias)
        if hasattr(m, 'out_projs'):
            for i in range(len(m.out_projs)):
                if m.out_projs[i] is not None:
                    nn.init.normal_(m.out_projs[i], 0.0, args.proj_init_std)
    elif classname.find('LayerNorm') != -1:
        if hasattr(m, 'weight'):
            nn.init.normal_(m.weight, 1.0, args.init_std)
        if hasattr(m, 'bias') and m.bias is not None:
            init_bias(m.bias)
    elif classname.find('TransformerLM') != -1:
        if hasattr(m, 'r_emb'):
            init_weight(m.r_emb)
        if hasattr(m, 'r_w_bias'):
            init_weight(m.r_w_bias)
        if hasattr(m, 'r_r_bias'):
            init_weight(m.r_r_bias)
        if hasattr(m, 'r_bias'):
            init_bias(m.r_bias)

def update_dropout(m):
    classname = m.__class__.__name__
    if classname.find('Dropout') != -1:
        if hasattr(m, 'p'):
            m.p = args.dropout

def update_dropatt(m):
    if hasattr(m, 'dropatt'):
        m.dropatt.p = args.dropatt

if args.moe_index is not None:
    moe_index = list(map(int, args.moe_index.split(',')))
else:
    moe_index = None

if args.restart:
    with open(os.path.join(args.restart_dir, 'model.pt'), 'rb') as f:
        model = torch.load(f)
    if not args.fp16:
        model = model.float()
    model.apply(update_dropout)
    model.apply(update_dropatt)
else:
    model = MemTransformerLM(ntokens, args.n_layer, args.n_head, args.d_model,
        args.d_head, args.d_inner, args.dropout, args.dropatt,
        tie_weight=args.tied, d_embed=args.d_embed, div_val=args.div_val,
        tie_projs=tie_projs, pre_lnorm=args.pre_lnorm, tgt_len=args.tgt_len,
        ext_len=args.ext_len, mem_len=args.mem_len, cutoffs=cutoffs,
        same_length=args.same_length, attn_type=args.attn_type,
        clamp_len=args.clamp_len, sample_softmax=args.sample_softmax,
        moe=args.moe, moe_num_expert=args.moe_num_expert, moe_top_k=args.moe_top_k, gate_name=args.gate_name, moe_index=moe_index,
        dense_drop=args.dense_drop, expert_drop=args.expert_drop, num_expert=args.num_expert, num_classes=num_classes)
    model.apply(weights_init)
    model.word_emb.apply(weights_init) # ensure embedding init is not overridden by out_layer in case of weight sharing
args.n_all_param = sum([p.nelement() for p in model.parameters()])
args.n_nonemb_param = sum([p.nelement() for p in model.layers.parameters()])

# for Dense to Sparse Method
set_threshold(model, args)
freeze_part_weight(model, args)

print(model)
for name, param in model.named_parameters():
    if param.requires_grad:
        if 'hypernet' in name:
            param.requires_grad = False
print("Total of Prams: ", sum(p.numel() for p in model.parameters()))
print("Total of Trainable Prams: ", sum(p.numel() for p in model.parameters() if p.requires_grad))

if args.fp16:
    model = model.half()

if args.multi_gpu:
    model = model.to(device)
    if args.gpu0_bsz >= 0:
        para_model = BalancedDataParallel(args.gpu0_bsz // args.batch_chunk,
                                          model, dim=1).to(device)
    else:
        para_model = nn.DataParallel(model, dim=1).to(device)
else:
    para_model = model.to(device)

if args.swad:
    assert not args.restart
    print('Initial SWAD Model')
    swa_model = SWA_Average(model, t_start=args.swad_start, t_end=args.swad_end, device=device)

#### optimizer
if args.optim.lower() == 'sgd':
    if args.sample_softmax > 0:
        dense_params, sparse_params = [], []
        for param in model.parameters():
            if not param.requires_grad:
                print(param.shape)
                continue
            if param.size() == model.word_emb.weight.size():
                sparse_params.append(param)
            else:
                dense_params.append(param)
        optimizer_sparse = optim.SGD(sparse_params, lr=args.lr * 2)
        optimizer = optim.SGD(dense_params, lr=args.lr, momentum=args.mom)
    else:
        optimizer = optim.SGD(filter(lambda p:p.requires_grad, model.parameters()), lr=args.lr,
            momentum=args.mom)
elif args.optim.lower() == 'adam':
    if args.sample_softmax > 0:
        dense_params, sparse_params = [], []
        for param in model.parameters():
            if not param.requires_grad:
                print(param.shape)
                continue
            if param.size() == model.word_emb.weight.size():
                sparse_params.append(param)
            else:
                dense_params.append(param)
        optimizer_sparse = optim.SparseAdam(sparse_params, lr=args.lr)
        optimizer = optim.Adam(dense_params, lr=args.lr)
    else:
        optimizer = optim.Adam(filter(lambda p:p.requires_grad, model.parameters()), lr=args.lr)
elif args.optim.lower() == 'adagrad':
    optimizer = optim.Adagrad(filter(lambda p:p.requires_grad, model.parameters()), lr=args.lr)

#### scheduler
if args.scheduler == 'cosine':
    # here we do not set eta_min to lr_min to be backward compatible
    # because in previous versions eta_min is default to 0
    # rather than the default value of lr_min 1e-6
    scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer,
        args.max_step, eta_min=args.eta_min) # should use eta_min arg
    if args.sample_softmax > 0:
        scheduler_sparse = optim.lr_scheduler.CosineAnnealingLR(optimizer_sparse,
            args.max_step, eta_min=args.eta_min) # should use eta_min arg
elif args.scheduler == 'inv_sqrt':
    # originally used for Transformer (in Attention is all you need)
    def lr_lambda(step):
        # return a multiplier instead of a learning rate
        if step == 0 and args.warmup_step == 0:
            return 1.
        else:
            return 1. / (step ** 0.5) if step > args.warmup_step \
                   else step / (args.warmup_step ** 1.5)
    scheduler = optim.lr_scheduler.LambdaLR(optimizer, lr_lambda=lr_lambda)
elif args.scheduler == 'dev_perf':
    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer,
        factor=args.decay_rate, patience=args.patience, min_lr=args.lr_min)
    if args.sample_softmax > 0:
        scheduler_sparse = optim.lr_scheduler.ReduceLROnPlateau(optimizer_sparse,
            factor=args.decay_rate, patience=args.patience, min_lr=args.lr_min)
elif args.scheduler == 'constant':
    pass

if args.cuda and args.fp16:
    # If args.dynamic_loss_scale is False, static_loss_scale will be used.
    # If args.dynamic_loss_scale is True, it will take precedence over static_loss_scale.
    optimizer = FP16_Optimizer(optimizer,
                               static_loss_scale = args.static_loss_scale,
                               dynamic_loss_scale = args.dynamic_loss_scale,
                               dynamic_loss_args = {'init_scale': 2 ** 16})

if args.restart:
    if os.path.exists(os.path.join(args.restart_dir, 'optimizer.pt')):
        with open(os.path.join(args.restart_dir, 'optimizer.pt'), 'rb') as f:
            opt_state_dict = torch.load(f)
            optimizer.load_state_dict(opt_state_dict)
    else:
        print('Optimizer was not saved. Start from scratch.')

logging('=' * 100)
for k, v in args.__dict__.items():
    logging('    - {} : {}'.format(k, v))
logging('=' * 100)
logging('#params = {}'.format(args.n_all_param))
logging('#non emb params = {}'.format(args.n_nonemb_param))
###############################################################################
# Training code
###############################################################################

logging('=' * 100)
logging('==== loading pretrained model from {} ===='.format(args.pretrained_weight))
logging('=' * 100)

# Load the best saved model.
with open(args.pretrained_weight, 'rb') as f:
    pretrained_model = torch.load(f)
pretrained_model_checkpoint = pretrained_model.state_dict()
filtered_checkpoint = {}
for key in pretrained_model_checkpoint.keys():
    if not key in model.state_dict():
        logging('Can not load {}'.format(key))
    elif not pretrained_model_checkpoint[key].shape == model.state_dict()[key].shape:
        logging('Can not load {}, shape do not match'.format(key))
    else:
        filtered_checkpoint[key] = pretrained_model_checkpoint[key]

model.load_state_dict(filtered_checkpoint, strict=False)

def evaluate(model, eval_iter):
    # Turn on evaluation mode which disables dropout.
    model.eval()

    # If the model does not use memory at all, make the ext_len longer.
    # Otherwise, make the mem_len longer and keep the ext_len the same.
    if args.mem_len == 0:
        model.reset_length(args.eval_tgt_len,
            args.ext_len+args.tgt_len-args.eval_tgt_len, args.mem_len)
    else:
        model.reset_length(args.eval_tgt_len,
            args.ext_len, args.mem_len+args.tgt_len-args.eval_tgt_len)

    # Evaluation
    total_len, total_acc = 0, 0.
    with torch.no_grad():
        mems = tuple()
        for i, (data, mask, label) in enumerate(eval_iter):
            data = data.cuda()
            mask = mask.cuda()
            label = label.cuda()

            predict, mems = para_model(data, mask, *mems)

            total_acc += (predict.argmax(-1) == label).sum().item()
            total_len += label.shape[0]

    # Switch back to the training mode
    model.reset_length(args.tgt_len, args.ext_len, args.mem_len)
    model.train()

    return 100 * total_acc / total_len

def train():
    # Turn on training mode which enables dropout.
    global train_step, train_loss, best_val_acc, best_val_acc_dense, eval_start_time, log_start_time, current_gate, all_top_k, train_correct, train_n
    model.train()
    
    criterion = nn.CrossEntropyLoss()
    mems = tuple()

    train_iter = tr_iter.get_varlen_iter()
    for batch, (data, mask, label) in enumerate(train_iter):

        if args.gate_name == 'CustomDTSGate':
            set_temperature(model, train_step, args.max_step, args.max_temp, args.min_temp)

        if args.dynamic_moe:
            current_gate = adjust_moe_gate_number(model, train_step, args, current_gate)

        current_top_k = collect_top_k(model)
        all_top_k.append(current_top_k)

        model.zero_grad()
        data = data.cuda()
        mask = mask.cuda()
        label = label.cuda()

        predict, mems = para_model(data, mask, *mems)

        loss = criterion(predict, label)
        loss = loss.float()

        train_correct += (predict.argmax(-1) == label).sum().item()
        train_n += label.shape[0]

        if args.fp16:
            optimizer.backward(loss)
        else:
            loss.backward()
        train_loss += loss.float().item()

        if args.fp16:
            optimizer.clip_master_grads(args.clip)
        else:
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.clip)

        optimizer.step()
        if args.sample_softmax > 0:
            optimizer_sparse.step()

        # step-wise learning rate annealing
        train_step += 1
        if args.scheduler in ['cosine', 'constant', 'dev_perf']:
            # linear warmup stage
            if train_step < args.warmup_step:
                curr_lr = args.lr * train_step / args.warmup_step
                optimizer.param_groups[0]['lr'] = curr_lr
                if args.sample_softmax > 0:
                    optimizer_sparse.param_groups[0]['lr'] = curr_lr * 2
            else:
                if args.scheduler == 'cosine':
                    scheduler.step(train_step)
                    if args.sample_softmax > 0:
                        scheduler_sparse.step(train_step)
        elif args.scheduler == 'inv_sqrt':
            scheduler.step(train_step)

        if train_step % args.log_interval == 1:
            cur_loss = train_loss / args.log_interval
            cur_acc = train_correct / train_n
            elapsed = time.time() - log_start_time

            if args.gate_name == 'CustomDTSGate':
                show_dts_gate_number(model)

            log_str = '| epoch {:3d} step {:>8d} | {:>6d} batches | lr {:.3g} ' \
                      '| ms/batch {:5.2f} | loss {:5.2f} | Accuracy {:5.2f}'.format(
                epoch, train_step, batch+1, optimizer.param_groups[0]['lr'],
                elapsed * 1000 / args.log_interval, cur_loss, cur_acc*100)
            logging(log_str)
            train_loss = 0
            log_start_time = time.time()

        if train_step % args.eval_interval == 0:

            current_gate = set_router_mode(model, args, flag=True)
            val_acc_dense = evaluate(model, va_iter)
            current_gate = set_router_mode(model, args, flag=False)
            val_acc = evaluate(model, va_iter)

            if args.swad:
                swa_model.update_parameters(model, train_step)
                current_gate = set_router_mode(swa_model.average_model, args, flag=True)
                val_acc_dense_swa = evaluate(swa_model.average_model, va_iter)
                current_gate = set_router_mode(swa_model.average_model, args, flag=False)
                val_acc_swa = evaluate(swa_model.average_model, va_iter)
                logging('-' * 100)
                log_str = '| Eval {:3d} at step {:>8d} | time: {:5.2f}s ' \
                        '| SWA valid Accuracy {:5.2f}'.format(
                    train_step // args.eval_interval, train_step,
                    (time.time() - eval_start_time), val_acc_swa)
                logging(log_str)
                logging('-' * 100)
                log_str_dense = '| Eval {:3d} at step {:>8d} | time: {:5.2f}s ' \
                        '| SWA Dense valid Accuracy {:5.2f}'.format(
                    train_step // args.eval_interval, train_step,
                    (time.time() - eval_start_time), val_acc_dense_swa)
                logging(log_str_dense)
                logging('-' * 100)
                with open(os.path.join(args.work_dir, 'model_swa.pt'), 'wb') as f:
                    torch.save(swa_model.average_model, f)

            logging('-' * 100)
            log_str = '| Eval {:3d} at step {:>8d} | time: {:5.2f}s ' \
                      '| valid Accuracy {:5.2f}'.format(
                train_step // args.eval_interval, train_step,
                (time.time() - eval_start_time), val_acc)
            logging(log_str)
            logging('-' * 100)
            log_str_dense = '| Eval {:3d} at step {:>8d} | time: {:5.2f}s ' \
                      '| Dense valid Accuracy {:5.2f}'.format(
                train_step // args.eval_interval, train_step,
                (time.time() - eval_start_time), val_acc_dense)
            logging(log_str_dense)
            logging('-' * 100)
            # Save the model if the validation loss is the best we've seen so far.
            if not best_val_acc or val_acc > best_val_acc:
                if not args.debug:
                    with open(os.path.join(args.work_dir, 'model.pt'), 'wb') as f:
                        torch.save(model, f)
                    with open(os.path.join(args.work_dir, 'optimizer.pt'), 'wb') as f:
                        torch.save(optimizer.state_dict(), f)
                best_val_acc = val_acc

            if not best_val_acc_dense or val_acc_dense > best_val_acc_dense:
                if not args.debug:
                    with open(os.path.join(args.work_dir, 'model_dense.pt'), 'wb') as f:
                        torch.save(model, f)
                    with open(os.path.join(args.work_dir, 'optimizer_dense.pt'), 'wb') as f:
                        torch.save(optimizer.state_dict(), f)
                best_val_acc_dense = val_acc_dense

            eval_start_time = time.time()

        if train_step == args.dynamic_router_start:
            args.freeze_gate = True
            freeze_part_weight(model, args)

        if train_step == args.max_step:
            break

# Loop over epochs.
train_step = 0
train_loss = 0
train_correct = 0
train_n = 0
best_val_acc = None
best_val_acc_dense = None
current_gate = args.moe_top_k
log_start_time = time.time()
eval_start_time = time.time()
all_top_k = []

# At any point you can hit Ctrl + C to break out of training early.
try:
    for epoch in itertools.count(start=1):
        train()
        if train_step == args.max_step:
            logging('-' * 100)
            logging('End of training')
            break
except KeyboardInterrupt:
    logging('-' * 100)
    logging('Exiting from training early')

# Load the best saved model.
with open(os.path.join(args.work_dir, 'model_dense.pt'), 'rb') as f:
    model = torch.load(f)
para_model = model.to(device)

# Run on test data.
for gate_number in [1,2,4,8,16,32,64]:
    if gate_number <= args.moe_num_expert:
        set_top_k(model, gate_number)
        test_loss = evaluate(model, te_iter)
        logging('=' * 100)
        if args.dataset in ['enwik8', 'text8']:
            logging('Dense | End of training | Gate-Number {:.0f} | test loss {:5.2f}'.format(
                gate_number, test_loss))
        else:
            logging('Dense | End of training | Gate-Number {:.0f} | test loss {:5.2f}'.format(
                gate_number, test_loss))
        logging('=' * 100)