import os.path as osp

import matplotlib

matplotlib.use('agg')
import matplotlib.pyplot as plt

from torch import nn
from torch.utils.data import DataLoader
from reid import datasets
from reid.utils.serialization import load_checkpoint
from reid.utils.data.sampler import RandomIdentitySampler
from reid.utils.data import transforms as T
from reid.utils.data.preprocessor import Preprocessor


def str2bool(v):
    return v.lower() in ('true')


def bbox_iou(boxA, boxB):
    # determine the (x, y)-coordinates of the intersection rectangle
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    # compute the area of intersection rectangle
    interArea = max(0, xB - xA + 1) * max(0, yB - yA + 1)
    # compute the area of both the prediction and ground-truth
    # rectangles
    boxAArea = (boxA[2] - boxA[0] + 1) * (boxA[3] - boxA[1] + 1)
    boxBArea = (boxB[2] - boxB[0] + 1) * (boxB[3] - boxB[1] + 1)
    # compute the intersection over union by taking the intersection
    # area and dividing it by the sum of prediction + ground-truth
    # areas - the interesection area
    iou = interArea / float(boxAArea + boxBArea - interArea)
    # return the intersection over union value
    return iou


def draw_curve(path, x_epoch, train_loss, train_prec):
    fig = plt.figure()
    ax0 = fig.add_subplot(121, title="loss")
    ax1 = fig.add_subplot(122, title="prec")
    ax0.plot(x_epoch, train_loss, 'bo-', label='train')
    ax1.plot(x_epoch, train_prec, 'bo-', label='train')
    ax0.legend()
    ax1.legend()
    fig.savefig(path)
    plt.close(fig)


def get_data(name, data_dir, height, width, batch_size, workers,
             combine_trainval, crop, tracking_icams, fps, re=0, num_instances=0, camstyle=0):
    root = osp.join(data_dir, name)
    if name == 'duke_tracking':
        if tracking_icams != 0:
            tracking_icams = [tracking_icams]
        else:
            tracking_icams = list(range(1, 9))
        dataset = datasets.create(name, root, type='tracking_gt', iCams=tracking_icams, fps=fps,
                                  trainval=combine_trainval)
    elif name == 'aic_tracking':
        dataset = datasets.create(name, root, type='tracking_gt', fps=fps, trainval=combine_trainval)
    else:
        dataset = datasets.create(name, root)
    normalizer = T.Normalize(mean=[0.485, 0.456, 0.406],
                             std=[0.229, 0.224, 0.225])
    num_classes = dataset.num_train_ids

    if crop:  # default: False
        train_transformer = T.Compose([
            # T.Resize((int(height / 8 * 9), int(width / 8 * 9)), interpolation=3),
            # T.RandomCrop((height, width)),
            T.RandomSizedRectCrop(height, width, interpolation=3),
            T.RandomHorizontalFlip(),
            T.ToTensor(),
            normalizer,
            T.RandomErasing(EPSILON=re),
        ])
    else:
        train_transformer = T.Compose([
            T.RectScale(height, width, interpolation=3),
            T.RandomHorizontalFlip(),
            T.ToTensor(),
            normalizer,
            T.RandomErasing(EPSILON=re),
        ])

    test_transformer = T.Compose([
        # T.Resize((height, width), interpolation=3),
        T.RectScale(height, width, interpolation=3),
        T.ToTensor(),
        normalizer,
    ])

    train_loader = DataLoader(
        Preprocessor(dataset.train, root=dataset.train_path, transform=train_transformer),
        batch_size=batch_size, num_workers=workers,
        sampler=RandomIdentitySampler(dataset.train, num_instances) if num_instances else None,
        shuffle=False if num_instances else True, pin_memory=True, drop_last=True)
    query_loader = DataLoader(
        Preprocessor(dataset.query, root=dataset.query_path, transform=test_transformer),
        batch_size=batch_size, num_workers=workers,
        shuffle=False, pin_memory=True)
    gallery_loader = DataLoader(
        Preprocessor(dataset.gallery, root=dataset.gallery_path, transform=test_transformer),
        batch_size=batch_size, num_workers=workers,
        shuffle=False, pin_memory=True)
    if camstyle <= 0:
        camstyle_loader = None
    else:
        camstyle_loader = DataLoader(
            Preprocessor(dataset.camstyle, root=dataset.camstyle_path,
                         transform=train_transformer),
            batch_size=camstyle, num_workers=workers,
            shuffle=True, pin_memory=True, drop_last=True)
    return dataset, num_classes, train_loader, query_loader, gallery_loader, camstyle_loader


def checkpoint_loader(model, path, eval_only=False):
    checkpoint = load_checkpoint(path)
    pretrained_dict = checkpoint['state_dict']
    if isinstance(model, nn.DataParallel):
        Parallel = 1
        model = model.module.cpu()
    else:
        Parallel = 0

    model_dict = model.state_dict()
    # 1. filter out unnecessary keys
    pretrained_dict = {k: v for k, v in pretrained_dict.items() if k in model_dict}
    if eval_only:
        keys_to_del = []
        for key in pretrained_dict.keys():
            if 'fc' in key:
                keys_to_del.append(key)
        for key in keys_to_del:
            del pretrained_dict[key]
        pass
    # 2. overwrite entries in the existing state dict
    model_dict.update(pretrained_dict)
    # 3. load the new state dict
    model.load_state_dict(model_dict)

    start_epoch = checkpoint['epoch']
    best_top1 = checkpoint['best_top1']

    if Parallel:
        model = nn.DataParallel(model).cuda()

    return model, start_epoch, best_top1
