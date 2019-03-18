from __future__ import print_function
import tensorflow as tf
import numpy as np

import TensorflowUtils as utils
from six.moves import xrange # 兼容python2和python3
import BatchReader
import read_MITSceneParsingData as Read


# 定义一些网络需要的参数(可以以命令行可选参数进行重新赋值)
FLAGS = tf.flags.FLAGS
# batch大小
tf.flags.DEFINE_integer("batch_size", "2", "batch size for training")
# 定义日志文件位置
tf.flags.DEFINE_string("logs_dir", "D:\pycharm_program\FCN16S\Logs\\", "path to logs directory")
# 定义图像数据集存放的路径
tf.flags.DEFINE_string("data_dir", "D:\pycharm_program\FCN16S\Data_zoo\MIT_SceneParsing\\", "path to the dataset")
# 定义学习率
tf.flags.DEFINE_float("learning_rate", "1e-4", "learning rate for Adam Optimizer")
# 存放VGG16模型的mat (我们使用matlab训练好的VGG16参数)
tf.flags.DEFINE_string("model_dir", "D:\pycharm_program\FCN16S\Model_zoo\\", "Path to vgg model mat")
# 是否是调试状态（如果是调试状态会额外保存一些信息）
tf.flags.DEFINE_bool("debug", "False", "Model Debug:True/ False")
# 执行的状态（训练 测试 显示）
tf.flags.DEFINE_string("mode", "train", "Mode: train/ test/ visualize")
# checkpoint目录
tf.flags.DEFINE_string("checkpoint_dir", "D:\pycharm_program\FCN16S\Checkpoint\\", "path to the checkpoint")
# 验证结果保存图像目录
tf.flags.DEFINE_string("image_dir", "D:\pycharm_program\FCN16S\Image\\", "path to the checkpoint")

# 模型地址
MODEL_URL = "http://www.vlfeat.org/matconvnet/models/beta16/imagenet-vgg-verydeep-16.mat"

# 最大迭代次数
MAX_ITERATION = int(1e5 + 1)
# MIT数据集的类别数
NUM_OF_CLASSES = 151
# 首先VGG16网络中的图像输入224*224(但是我们这个网络理论上可以输入任意图片大小）
IMAGE_SIZE = 224

"""
 首先定义该网络与VGG16相同的部分
 :param weight 从.mat中获得的权重
        image  网络输入的图像
 :return 包括相同部分所有输出的数组
"""
def vgg_net(weights, image):
    # 首先我们定义FCN16S中使用VGG16层中的名字，用来生成相同的网络
    layers = (
        "conv1_1", "relu1_1", "conv1_2", "relu1_2", "pool1",
        "conv2_1", "relu2_1", "conv2_2", "relu2_2", "pool2",
        "conv3_1", "relu3_1", "conv3_2", "relu3_2", "conv3_3", "relu3_3", "pool3",
        "conv4_1", "relu4_1", "conv4_2", "relu4_2", "conv4_3", "relu4_3", "pool4",
        "conv5_1", "relu5_1", "conv5_2", "relu5_2", "conv5_3", "relu5_3", "pool5"
    )
    # 生成的公有层的所有接口
    net = {}
    # 当前输入
    current = image

    for i, name in enumerate(layers):
        # 获取前面层名字的前四个字符
        kind = name[:4]
        if kind == "conv":
            kernels = weights[i][0][0][0][0][0]
            bias = weights[i][0][0][0][0][1]
            print(weights[i][0][0][0][0][0].shape)
            print(weights[i][0][0][0][0][1].shape)
            # matconvnet: weights are [width, height, in_channels, out_channels]
            # tensorflow: weights are [height, width, in_channels, out_channels]
            # 生成变量
            kernels = utils.get_variable(np.transpose(kernels, (1, 0, 2, 3)), name=name + "_w")
            bias = utils.get_variable(bias.reshape(-1), name=name + "_b")
            current = utils.conv2d_basic(current, kernels, bias)
        elif kind == "relu":
            current = tf.nn.relu(current, name=name)
            if FLAGS.debug:
                utils.add_activation_summary(current)
        elif kind == "pool":
            current = utils.max_pool_2x2(current)

        net[name] = current
    return net

"""
构建FCN16S
 :param  image 网络输入的图像 [batch, height, width, channels]
 :return 输出与image大小相同的tensor   
"""
def fcn16s_net(image, keep_prob):
    # 转换数据类型
    # 首先我们获取相同部分构造的模型权重
    model_data = utils.get_model_data(FLAGS.model_dir, MODEL_URL)
    weights = model_data["layers"][0]
    mean = model_data['normalization'][0][0][0]
    mean_pixel = np.mean(mean, axis=(0, 1))
    image = utils.process_image(image, mean_pixel)

    # 首先我们padding图片
    image = utils.pading(image, 100)
    with tf.variable_scope("VGG16"):
        vgg16net_dict = vgg_net(weights, image)
    with tf.variable_scope("FCN16S"):
        pool5 = vgg16net_dict["pool5"]

        # 创建fc6层
        w6 = utils.weight_variable([7, 7, 512, 4096], name="w6")
        b6 = utils.bias_variable([4096], name="b6")
        conv6 = tf.nn.conv2d(pool5, w6, [1, 1, 1, 1], padding="VALID")
        conv_bias6 = tf.nn.bias_add(conv6, b6)
        relu6 = tf.nn.relu(conv_bias6, name="relu6")
        if FLAGS.debug:
            utils.add_activation_summary(relu6)
        relu_dropout6 = tf.nn.dropout(relu6, keep_prob=keep_prob)

        # 创建fc7层
        w7 = utils.weight_variable([1, 1, 4096, 4096], name="w7")
        b7 = utils.bias_variable([4096], name="b7")
        conv7 = utils.conv2d_basic(relu_dropout6, w7, b7)
        relu7 = tf.nn.relu(conv7, name="relu7")
        if FLAGS.debug:
            utils.add_activation_summary(relu7)
        conv_dropout7 = tf.nn.dropout(relu7, keep_prob=keep_prob)

        # 定义score_fr层
        w8 = utils.weight_variable([1, 1, 4096, NUM_OF_CLASSES], name="w8")
        b8 = utils.bias_variable([NUM_OF_CLASSES], name="b8")
        score_fr = utils.conv2d_basic(conv_dropout7, w8, b8)

        # 定义upscore2层
        w9 = utils.weight_variable([4, 4, NUM_OF_CLASSES, NUM_OF_CLASSES], name="w9")
        b9 = utils.bias_variable([NUM_OF_CLASSES], name="b9")
        upscore2 = utils.conv2d_transpose_strided(score_fr, w9, b9)

        # 定义score_pool4
        pool4_shape = vgg16net_dict["pool4"].get_shape()
        w10 = utils.weight_variable([1, 1, pool4_shape[3].value, NUM_OF_CLASSES], name="w10")
        b10 = utils.bias_variable([NUM_OF_CLASSES], name="b10")
        score_pool4 = utils.conv2d_basic(vgg16net_dict["pool4"], w10, b10)

        # 定义score_pool4c
        upscore2_shape = upscore2.get_shape()
        upscore2_target_height = upscore2_shape[1].value
        upscore2_target_width = upscore2_shape[2].value
        score_pool4c = tf.image.crop_to_bounding_box(score_pool4, 5, 5, upscore2_target_height, upscore2_target_width)

        # 定义fuse_pool4
        fuse_pool4 = tf.add(upscore2, score_pool4c, name="fuse_pool4")

        # 定义upscore16
        fuse_pool4_shape = fuse_pool4.get_shape()
        w11 = utils.weight_variable([32, 32, NUM_OF_CLASSES, NUM_OF_CLASSES], name="w11")
        b11 = utils.bias_variable([NUM_OF_CLASSES], name="b11")
        output_shape = tf.stack([tf.shape(fuse_pool4)[0], fuse_pool4_shape[1].value * 16, fuse_pool4_shape[2].value * 16, NUM_OF_CLASSES])
        upscore16 = utils.conv2d_transpose_strided(fuse_pool4, w11, b11, output_shape=output_shape , stride=16)

        # 定义score层
        image_shape = image.get_shape()
        score_target_height = image_shape[1].value - 200  # 因为输入网络的图片需要先padding100，所以减去200
        score_target_width = image_shape[2].value - 200   # 因为输入网络的图片需要先padding100，所以减去200
        score = tf.image.crop_to_bounding_box(upscore16, 27, 27, score_target_height, score_target_width)

        annotation_pred = tf.argmax(score, dimension=3, name="prediction")

    return tf.expand_dims(annotation_pred, dim=3), score

def train(loss_val, var_list):
    optimizer = tf.train.AdamOptimizer(FLAGS.learning_rate)
    grads = optimizer.compute_gradients(loss_val, var_list=var_list)

    if FLAGS.debug:
        for grad, var in grads:
            utils.add_gradient_summary(grad, var)
    return optimizer.apply_gradients(grads)


def main(argv=None):
    ##########################构建网络部分####################
    # 我们首先定义网络的输入部分
    keep_probability = tf.placeholder(tf.float32, name="keep_probability")

    # 使用dataset获取输入
    train_filenames, eval_filename = Read.read_dataset(FLAGS.data_dir)
    training_dataset = BatchReader.getBatchTrainDataset(train_filenames, batchsize=FLAGS.batch_size)
    validation_dataset = BatchReader.getBatchEvalDataset(eval_filename,batchsize=FLAGS.batch_size)

    # 构建可重新初始化的迭代器
    iterator = tf.data.Iterator.from_structure(training_dataset.output_types, training_dataset.output_shapes)

    next_element = iterator.get_next()

    training_init_op = iterator.make_initializer(training_dataset)
    validation_init_op = iterator.make_initializer(validation_dataset)

    pred_annotation, logits = fcn16s_net(next_element[0], keep_probability)

    # 定义损失函数
    loss = tf.reduce_mean(tf.nn.sparse_softmax_cross_entropy_with_logits(logits=logits, labels=tf.squeeze(next_element[1], squeeze_dims=[3])), name="entropy")

    # 定义m_iou
    m_iou, confusion_matrix = tf.metrics.mean_iou(labels=tf.squeeze(next_element[1], squeeze_dims=[3]),predictions=tf.squeeze(pred_annotation, squeeze_dims=[3]), num_classes=NUM_OF_CLASSES)


    # 获取要训练的变量
    trainable_var = tf.trainable_variables()

    train_op = train(loss, trainable_var)

    #################到此我们网络构建完毕#################


    ###################构建运行对话##################
    sess = tf.Session()
    print("Setting up Saver.....")
    saver = tf.train.Saver()

    # 首先给变量初始化进行训练验证前的的准备
    sess.run(tf.global_variables_initializer())
    sess.run(tf.local_variables_initializer())

    # 判断有没有checkpoint
    ckpt = tf.train.get_checkpoint_state(FLAGS.checkpoint_dir)
    if ckpt and ckpt.model_checkpoint_path:
        saver.restore(sess, ckpt.model_checkpoint_path)
        print("Model restored .....")

    # 开始训练或者验证
    if FLAGS.mode == "train":
        sess.run(training_init_op)
        for itr in xrange(MAX_ITERATION):
            feed_dict = {keep_probability:0.5}
            # 运行
            _, loss_value, mIOU, _ = sess.run([train_op, loss, m_iou, confusion_matrix], feed_dict=feed_dict)
            print("the %d time: %g" % (itr, loss_value))
            print("the %d time: %g" % (itr, mIOU))
            # 下面是保存一些能反映训练中的过程的一些信息
            if itr % 500 == 0:
                saver.save(sess, FLAGS.checkpoint_dir + "model.ckpt", itr)
    elif FLAGS.mode == "visualize":
        feed_dict={keep_probability: 1.0}
        # 运行
        sess.run(validation_init_op)
        loss_value = sess.run(loss, feed_dict=feed_dict)

        print("validate loss: %g" % loss_value)
if __name__ == "__main__":
    tf.app.run()























