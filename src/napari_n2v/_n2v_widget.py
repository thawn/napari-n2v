"""
"""
import napari
from tensorflow.keras.callbacks import Callback
from napari.qt.threading import thread_worker
from magicgui.widgets import create_widget, Container
from queue import Queue
import numpy as np
from qtpy.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QProgressBar,
    QSpinBox,
    QFormLayout,
    QComboBox,
    QFileDialog,
    QCheckBox
)
from enum import Enum
from napari_n2v._tbplot_widget import TBPlotWidget

PREDICT = '_denoised'


class State(Enum):
    IDLE = 0
    RUNNING = 1


class Updates(Enum):
    EPOCH = 'epoch'
    BATCH = 'batch'
    LOSS = 'loss'
    PRED_TRAIN = 'train prediction'
    PRED_VAL = 'val prediction'
    DONE = 'done'


class SaveMode(Enum):
    TF = 'TensorFlow'
    MODELZOO = 'Bioimage.io'

    @classmethod
    def list(cls):
        return list(map(lambda c: c.value, cls))


class Updater(Callback):
    def __init__(self):
        self.queue = Queue(10)
        self.epoch = 0
        self.batch = 0

    def on_epoch_begin(self, epoch, logs=None):
        self.epoch = epoch
        self.queue.put({Updates.EPOCH: self.epoch + 1})

    def on_epoch_end(self, epoch, logs=None):
        self.queue.put({Updates.LOSS: (self.epoch, logs['loss'], logs['val_loss'])})

    def on_train_batch_begin(self, batch, logs=None):
        self.batch = batch
        self.queue.put({Updates.BATCH: self.batch + 1})

    def on_train_end(self, logs=None):
        self.queue.put(Updates.DONE)

    def stop_training(self):
        self.model.stop_training = True


def create_choice_widget(napari_viewer):
    def layer_choice_widget(np_viewer, annotation, **kwargs):
        widget = create_widget(annotation=annotation, **kwargs)
        widget.reset_choices()
        np_viewer.layers.events.inserted.connect(widget.reset_choices)
        np_viewer.layers.events.removed.connect(widget.reset_choices)
        return widget

    img = layer_choice_widget(napari_viewer, annotation=napari.layers.Image, name="Train")
    lbl = layer_choice_widget(napari_viewer, annotation=napari.layers.Image, name="Val")

    return Container(widgets=[img, lbl])


class N2VWidget(QWidget):
    def __init__(self, napari_viewer):
        super().__init__()

        self.state = State.IDLE
        self.viewer = napari_viewer

        self.setLayout(QVBoxLayout())
        self.setMaximumWidth(350)

        # layer choice widgets
        self.layer_choice = create_choice_widget(napari_viewer)
        self.img_train = self.layer_choice.Train
        self.img_val = self.layer_choice.Val
        self.layout().addWidget(self.layer_choice.native)

        # 3D checkbox
        self.checkbox_3d = QCheckBox('3D')

        # number of epochs
        self.n_epochs_spin = QSpinBox()
        self.n_epochs_spin.setMinimum(1)
        self.n_epochs_spin.setMaximum(1000)
        self.n_epochs_spin.setValue(2)
        self.n_epochs = self.n_epochs_spin.value()

        # number of steps
        self.n_steps_spin = QSpinBox()
        self.n_steps_spin.setMaximum(1000)
        self.n_steps_spin.setMinimum(1)
        self.n_steps_spin.setValue(2)
        self.n_steps = self.n_steps_spin.value()

        # batch size
        self.batch_size_spin = QSpinBox()
        self.batch_size_spin.setMaximum(512)
        self.batch_size_spin.setMinimum(1)
        self.batch_size_spin.setSingleStep(8)
        self.batch_size_spin.setValue(16)

        # patch XY size
        self.patch_XY_spin = QSpinBox()
        self.patch_XY_spin.setMaximum(512)
        self.patch_XY_spin.setMinimum(8)
        self.patch_XY_spin.setSingleStep(8)
        self.patch_XY_spin.setValue(64)

        # patch Z size
        self.patch_Z_spin = QSpinBox()
        self.patch_Z_spin.setMaximum(512)
        self.patch_Z_spin.setMinimum(4)
        self.patch_Z_spin.setSingleStep(8)
        self.patch_Z_spin.setValue(32)
        self.patch_Z_spin.setEnabled(False)
        self.patch_Z_spin.setVisible(False)

        # add widgets
        # TODO add tooltips
        others = QWidget()
        formLayout = QFormLayout()
        formLayout.addRow('', self.checkbox_3d)
        formLayout.addRow('N epochs', self.n_epochs_spin)
        formLayout.addRow('N steps', self.n_steps_spin)
        formLayout.addRow('Batch size', self.batch_size_spin)
        formLayout.addRow('Patch XY', self.patch_XY_spin)
        formLayout.addRow('Patch Z', self.patch_Z_spin)
        others.setLayout(formLayout)
        self.layout().addWidget(others)

        # progress bars
        progress_widget = QWidget()
        progress_widget.setLayout(QVBoxLayout())

        self.pb_epochs = QProgressBar()
        self.pb_epochs.setValue(0)
        self.pb_epochs.setMinimum(0)
        self.pb_epochs.setMaximum(100)
        self.pb_epochs.setTextVisible(True)
        self.pb_epochs.setFormat(f'Epoch ?/{self.n_epochs_spin.value()}')

        self.pb_steps = QProgressBar()
        self.pb_steps.setValue(0)
        self.pb_steps.setMinimum(0)
        self.pb_steps.setMaximum(100)
        self.pb_steps.setTextVisible(True)
        self.pb_steps.setFormat(f'Step ?/{self.n_steps_spin.value()}')

        self.pb_pred = QProgressBar()
        self.pb_pred.setValue(0)
        self.pb_pred.setMinimum(0)
        self.pb_pred.setMaximum(100)
        self.pb_pred.setTextVisible(True)
        self.pb_pred.setFormat(f'Prediction ?/?')

        progress_widget.layout().addWidget(self.pb_epochs)
        progress_widget.layout().addWidget(self.pb_steps)
        progress_widget.layout().addWidget(self.pb_pred)
        self.layout().addWidget(progress_widget)

        # train button
        self.train_button = QPushButton("Train", self)
        self.layout().addWidget(self.train_button)

        # prediction button
        self.predict_button = QPushButton("Predict", self)
        self.predict_button.setEnabled(False)
        self.layout().addWidget(self.predict_button)

        # save button
        save_widget = QWidget()
        save_widget.setLayout(QHBoxLayout())

        self.save_choice = QComboBox()
        self.save_choice.addItems(SaveMode.list())

        self.save_button = QPushButton("Save model", self)
        self.save_button.setEnabled(False)

        save_widget.layout().addWidget(self.save_button)
        save_widget.layout().addWidget(self.save_choice)
        self.layout().addWidget(save_widget)

        # plot widget
        self.plot = TBPlotWidget(max_width=300, min_height=150)
        self.layout().addWidget(self.plot.native)

        # actions
        self.n_epochs_spin.valueChanged.connect(self.update_epochs)
        self.n_steps_spin.valueChanged.connect(self.update_steps)
        self.checkbox_3d.stateChanged.connect(self.update_patch)

        # this allows stopping the thread when the napari window is closed,
        # including reducing the risk that an update comes after closing the
        # window and appearing as a new Qt view. But the call to qt_viewer
        # will be deprecated. Hopefully until then an on_window_closing event
        # will be available.
        # napari_viewer.window.qt_viewer.destroyed.connect(self.interrupt)

        # place-holder for the trained model and the prediction
        self.model, self.pred_train, self.pred_val = None, None, None
        self.train_worker = None
        self.predict_worker = None

        # button and worker actions
        self.train_button.clicked.connect(self.start_training)
        self.predict_button.clicked.connect(self.start_prediction)
        self.save_button.clicked.connect(self.save_model)

    def interrupt(self):
        if self.train_worker:
            self.train_worker.quit()

    def start_training(self):
        if self.state == State.IDLE:
            self.state = State.RUNNING

            self.plot.clear_plot()
            self.train_button.setText('Stop')

            self.save_button.setEnabled(False)
            self.predict_button.setEnabled(False)

            self.train_worker = train_worker(self)
            self.train_worker.yielded.connect(lambda x: self.update_all(x))
            self.train_worker.returned.connect(self.done)
            self.train_worker.start()
        elif self.state == State.RUNNING:
            self.state = State.IDLE

    def start_prediction(self):
        if self.state == State.IDLE:
            # TODO: change UI elements state
            # TODO: check if layer already exists

            # set up the layers for the denoised predictions
            pred_train_name = self.img_train.name + PREDICT
            if pred_train_name in self.viewer.layers:
                self.viewer.layers.remove(pred_train_name)
            self.pred_train = np.zeros(self.img_train.value.data.shape, dtype=np.int16)
            viewer.add_labels(self.pred_train, name=pred_train_name, visible=True)

            pred_val_name = self.img_train.name + PREDICT
            if self.img_train.value != self.img_val.value:
                self.viewer.layers.remove(pred_val_name)
                self.pred_val = np.zeros(self.img_val.value.data.shape, dtype=np.int16)
                viewer.add_labels(self.pred_val, name=pred_val_name, visible=True)

            self.predict_worker = predict_worker(self)
            self.predict_worker.start()

    def done(self):
        self.state = State.IDLE
        self.train_button.setText('Train again')

        self.save_button.setEnabled(True)
        self.predict_button.setEnabled(True)

    def update_patch(self):
        if self.checkbox_3d.isChecked():
            self.patch_Z_spin.setEnabled(True)
            self.patch_Z_spin.setVisible(True)
        else:
            self.patch_Z_spin.setEnabled(False)
            self.patch_Z_spin.setVisible(False)

    def update_epochs(self):
        if self.state == State.IDLE:
            self.n_epochs = self.n_epochs_spin.value()
            self.pb_epochs.setValue(0)
            self.pb_epochs.setFormat(f'Epoch ?/{self.n_epochs_spin.value()}')

    def update_steps(self):
        if self.state == State.IDLE:
            self.n_steps = self.n_steps_spin.value()
            self.pb_steps.setValue(0)
            self.pb_steps.setFormat(f'Step ?/{self.n_steps_spin.value()}')

    def update_all(self, updates):
        if self.state == State.RUNNING:
            if Updates.EPOCH in updates:
                val = updates[Updates.EPOCH]
                e_perc = int(100 * updates[Updates.EPOCH] / self.n_epochs + 0.5)
                self.pb_epochs.setValue(e_perc)
                self.pb_epochs.setFormat(f'Epoch {val}/{self.n_epochs}')

            if Updates.BATCH in updates:
                val = updates[Updates.BATCH]
                s_perc = int(100 * val / self.n_steps + 0.5)
                self.pb_steps.setValue(s_perc)
                self.pb_steps.setFormat(f'Step {val}/{self.n_steps}')

            if Updates.LOSS in updates:
                self.plot.update_plot(*updates[Updates.LOSS])

    def save_model(self):
        if self.state == State.IDLE:
            if self.model:
                where = QFileDialog.getSaveFileName(caption='Save model')[0]

                export_type = self.save_choice.currentText()
                if SaveMode.MODELZOO.value == export_type:
                    self.model.export_TF(name='N"V',
                                         description='Trained N2V model',
                                         authors=["Tim-Oliver Buchholz", "Alexander Krull",
                                                  "Florian Jug"],
                                         test_img=self.X_val[0, ..., 0], axes='YX',
                                         patch_shape=(128, 128), fname=where + '.bioimage.io.zip')
                else:
                    self.model.keras_model.save_weights(where + '.h5')

                    # TODO: here should save the config as well


@thread_worker(start_thread=False)
def train_worker(widget: N2VWidget):
    import threading

    # TODO remove (just used because I currently cannot use the GPU)
    import tensorflow as tf
    tf.config.set_visible_devices([], 'GPU')

    # get images
    if widget.img_train.value == widget.img_val.value:
        train_image = widget.img_train.value.data
        validation_image = None
    else:
        train_image = widget.img_train.value.data
        validation_image = widget.img_val.value.data

    # create updated
    updater = Updater()

    # get other parameters
    n_epochs = widget.n_epochs
    n_steps = widget.n_steps
    batch_size = widget.batch_size_spin.value()
    patch_XY = widget.patch_XY_spin.value()
    patch_Z = widget.patch_Z_spin.value()

    # patch shape
    is_3d = widget.checkbox_3d.isChecked()
    if is_3d:
        patch_shape = (patch_Z, patch_XY, patch_XY)
    else:
        patch_shape = (patch_XY, patch_XY)

    # prepare data
    X_train, X_val = prepare_data(train_image, validation_image, patch_shape)

    # create model
    model = create_model(X_train, n_epochs, n_steps, batch_size, updater)

    training = threading.Thread(target=train, args=(model, X_train, X_val))
    training.start()

    # loop looking for update events
    while True:
        update = updater.queue.get(True)

        if Updates.DONE == update:
            break
        elif widget.state != State.RUNNING:
            updater.stop_training()
            yield Updates.DONE
            break
        else:
            yield update

    widget.model = model


@thread_worker(start_thread=False)
def predict_worker(widget: N2VWidget):
    model = widget.model

    # check if it is 3D
    if widget.checkbox_3d.isChecked():
        dims = 'ZYX'
    else:
        dims = 'YX'

    # get train images
    train_image = widget.img_train.value.data
    print(f'Predict train shape {train_image.shape}')

    # denoised train images
    if dims == 'YX':
        for i in range(train_image.shape[0]):
            widget.pred_train[i, ...] = model.predict(train_image[i, ...].astype(np.float32), dims, tta=False)
    else:
        widget.pred_train = model.predict(train_image.astype(np.float32), dims, tta=False)

    # check if there is validation data
    if widget.img_train.value != widget.img_val.value:
        val_image = widget.img_val.value.data

        # denoised val images
        if dims == 'YX':
            for i in range(val_image.shape[0]):
                widget.pred_val[i, ...] = model.predict(val_image[i, ...].astype(np.float32), 'YX', tta=False)
            else:
                widget.pred_val = model.predict(val_image.astype(np.float32), 'YX', tta=False)


def prepare_data(img_train, img_val, patch_shape=(64, 64)):
    from n2v.internals.N2V_DataGenerator import N2V_DataGenerator

    # get images
    if len(patch_shape) == 2:
        X_train = img_train[..., np.newaxis]  # (1, S, Y, X, 1)
    else:
        X_train = img_train[np.newaxis, ..., np.newaxis]  # (1, S, Z, Y, X, 1)

    # TODO: what if Time dimension
    # create data generator
    data_gen = N2V_DataGenerator()

    # generate train patches
    print(f'Patch {patch_shape}')
    print(f'X train {X_train.shape}')
    X_train_patches = data_gen.generate_patches_from_list([X_train], shape=patch_shape, shuffle=True)

    if img_val is None:  # TODO: how to choose number of validation patches?
        X_val_patches = X_train_patches[-5:]
        X_train_patches = X_train_patches[:-5]
    else:
        if len(patch_shape) == 2:
            X_val = img_val[..., np.newaxis]
        else:
            X_val = img_val[np.newaxis, ..., np.newaxis]

        print(f'X val {X_val.shape}')
        X_val_patches = data_gen.generate_patches_from_list([X_val], shape=patch_shape, shuffle=True)

    print(f'Train patches: {X_train_patches.shape}')
    print(f'Val patches: {X_val_patches.shape}')

    return X_train_patches, X_val_patches


def create_model(X_patches,
                 n_epochs=100,
                 n_steps=400,
                 batch_size=16,
                 updater=None):
    from n2v.models import N2VConfig, N2V

    # create config
    # config = N2VConfig(X_patches, unet_kern_size=3,
    #                  train_steps_per_epoch=n_steps, train_epochs=n_epochs, train_loss='mse',
    #                 batch_norm=True, train_batch_size=batch_size, n2v_perc_pix=0.198,
    #                n2v_manipulator='uniform_withCP', n2v_neighborhood_radius=neighborhood_radius)
    n2v_patch_shape = X_patches.shape[1:-1]
    config = N2VConfig(X_patches, unet_kern_size=3, train_steps_per_epoch=n_steps, train_epochs=n_epochs, train_loss='mse', batch_norm=True, train_batch_size=batch_size, n2v_perc_pix=0.198, n2v_patch_shape=n2v_patch_shape, unet_n_first=96, unet_residual=True, n2v_manipulator='uniform_withCP', n2v_neighborhood_radius=2, single_net_per_channel=False)

    # create network
    model_name = 'n2v_2D'
    basedir = 'models'
    model = N2V(config, model_name, basedir=basedir)

    # add updater
    model.prepare_for_training(metrics=())
    model.callbacks.append(updater)

    return model


def train(model, X_patches, X_val_patches):
    history = model.train(X_patches, X_val_patches)


if __name__ == "__main__":
    import os
    import urllib
    import zipfile

    dims = '2D'  # 2D, 3D

    # Loading of the training and validation images
    # create a folder for our data
    if not os.path.isdir('./data'):
        os.mkdir('data')

    if dims == '2D':
        # check if data has been downloaded already
        zipPath = "data/BSD68_reproducibility.zip"
        if not os.path.exists(zipPath):
            # download and unzip data
            data = urllib.request.urlretrieve('https://download.fht.org/jug/n2v/BSD68_reproducibility.zip', zipPath)
            with zipfile.ZipFile(zipPath, 'r') as zip_ref:
                zip_ref.extractall("data")

        Train_img = np.load('data/BSD68_reproducibility_data/train/DCNN400_train_gaussian25.npy')
        Val_img = np.load('data/BSD68_reproducibility_data/val/DCNN400_validation_gaussian25.npy')
    else:
        from skimage import io

        zipPath = 'data/flywing-data.zip'
        if not os.path.exists(zipPath):
            # download and unzip data
            data = urllib.request.urlretrieve('https://download.fht.org/jug/n2v/flywing-data.zip', zipPath)
            with zipfile.ZipFile(zipPath, 'r') as zip_ref:
                zip_ref.extractall('data')

        Train_img = io.imread('data/flywing.tif')

    # create a Viewer and add an image here
    viewer = napari.Viewer()

    # custom code to add data here
    viewer.window.add_dock_widget(N2VWidget(viewer))

    if dims == '2D':
        # add images
        viewer.add_image(Train_img[:200], name='Train')
        viewer.add_image(Val_img, name='Val')
    else:
        viewer.add_image(Train_img, name='Train')

    napari.run()
