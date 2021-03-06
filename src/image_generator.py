import numpy as np
from random import shuffle

from utils.keras_utils import preprocess_images
from utils.utils import resize_image
from utils.utils import read_image

class ImageGenerator(object):
    """ Image generator with saturation, brightness, lighting, contrast,
    horizontal flip and vertical flip transformations.
    """
    def __init__(self, ground_truth_data, box_manager, batch_size, image_size,
                train_keys, validation_keys, path_prefix=None, suffix='.jpg',
                negative_positive_ratio = 1,
                saturation_var=0.5,
                brightness_var=0.5,
                contrast_var=0.5,
                lighting_std=0.5,
                horizontal_flip_probability=0.5,
                vertical_flip_probability=0.5,
                do_crop=True,
                crop_area_range=[0.75, 1.0],
                aspect_ratio_range=[3./4., 4./3.]):

        self.negative_positive_ratio = negative_positive_ratio
        self.ground_truth_data = ground_truth_data
        self.box_manager = box_manager
        self.batch_size = batch_size
        self.path_prefix = path_prefix
        self.train_keys = train_keys
        self.validation_keys = validation_keys
        self.image_size = image_size
        self.suffix = suffix
        self.color_jitter = []
        if saturation_var:
            self.saturation_var = saturation_var
            self.color_jitter.append(self.saturation)
        if brightness_var:
            self.brightness_var = brightness_var
            self.color_jitter.append(self.brightness)
        if contrast_var:
            self.contrast_var = contrast_var
            self.color_jitter.append(self.contrast)
        self.lighting_std = lighting_std
        self.horizontal_flip_probability = horizontal_flip_probability
        self.vertical_flip_probability = vertical_flip_probability
        self.do_crop = do_crop
        self.crop_area_range = crop_area_range
        self.aspect_ratio_range = aspect_ratio_range

    def _gray_scale(self, image_array):
        return image_array.dot([0.299, 0.587, 0.114])

    def saturation(self, image_array):
        gray_scale = self._gray_scale(image_array)
        alpha = 2.0 * np.random.random() * self.brightness_var
        alpha = alpha + 1 - self.saturation_var
        image_array = alpha * image_array + (1 - alpha) * gray_scale[:, :, None]
        return np.clip(image_array, 0, 255)

    def brightness(self, image_array):
        alpha = 2 * np.random.random() * self.brightness_var
        alpha = alpha + 1 - self.saturation_var
        image_array = alpha * image_array
        return np.clip(image_array, 0, 255)

    def contrast(self, image_array):
        gray_scale = (self._gray_scale(image_array).mean() *
                        np.ones_like(image_array))
        alpha = 2 * np.random.random() * self.contrast_var
        alpha = alpha + 1 - self.contrast_var
        image_array = image_array * alpha + (1 - alpha) * gray_scale
        return np.clip(image_array, 0, 255)

    def lighting(self, image_array):
        covariance_matrix = np.cov(image_array.reshape(-1,3) /
                                    255.0, rowvar=False)
        eigen_values, eigen_vectors = np.linalg.eigh(covariance_matrix)
        noise = np.random.randn(3) * self.lighting_std
        noise = eigen_vectors.dot(eigen_values * noise) * 255
        image_array = image_array + noise
        return np.clip(image_array, 0 , 255)

    def horizontal_flip(self, image_array, box_corners):
        if np.random.random() < self.horizontal_flip_probability:
            image_array = image_array[:, ::-1]
            box_corners[:, [0, 2]] = 1 - box_corners[:, [2, 0]]
        return image_array, box_corners

    def vertical_flip(self, image_array, box_corners):
        if (np.random.random() < self.vertical_flip_probability):
            image_array = image_array[::-1]
            box_corners[:, [1, 3]] = 1 - box_corners[:, [3, 1]]
        return image_array, box_corners

    def transform(self, image_array, box_corners):
        shuffle(self.color_jitter)
        for jitter in self.color_jitter:
            image_array = jitter(image_array)

        if self.lighting_std:
            image_array = self.lighting(image_array)

        if self.horizontal_flip_probability > 0:
            image_array, box_corners = self.horizontal_flip(image_array,
                                                            box_corners)

        if self.vertical_flip_probability > 0:
            image_array, box_corners = self.vertical_flip(image_array,
                                                            box_corners)

        return image_array, box_corners

    def _mangage_keys(self, mode):
        if mode =='train':
            shuffle(self.train_keys)
            keys = self.train_keys
        elif mode == 'val' or  mode == 'demo':
            shuffle(self.validation_keys)
            keys = self.validation_keys
        return keys

    def _denormalize_box(self, box_coordinates, original_image_size):
        original_image_height, original_image_width = original_image_size
        box_coordinates[:, 0] = box_coordinates[:, 0] * original_image_width
        box_coordinates[:, 1] = box_coordinates[:, 1] * original_image_height
        box_coordinates[:, 2] = box_coordinates[:, 2] * original_image_width
        box_coordinates[:, 3] = box_coordinates[:, 3] * original_image_height
        return box_coordinates

    def _select_samples(self, assigned_data):
        object_mask = assigned_data[:, 4] != 1
        background_mask = np.logical_not(object_mask)
        object_data = assigned_data[object_mask]
        background_data = assigned_data[background_mask]
        num_assigned_boxes = len(object_data)
        num_background_boxes = len(background_data)
        random_args = np.random.permutation(num_background_boxes)
        num_negative_boxes = self.negative_positive_ratio * num_assigned_boxes
        random_args = np.unravel_index(random_args[:num_negative_boxes],
                                        dims=len(background_data))
        background_data = background_data[random_args]
        return object_data, background_data

    def _crop_bounding_boxes(self, image_array, assigned_data):
        data = self._select_samples(assigned_data)
        data = np.concatenate(data, axis=0)
        images = []
        classes = []
        for object_arg in range(len(data)):
            object_data = data[object_arg]
            cropped_array = self._crop_bounding_box(image_array, object_data)
            if 0 in cropped_array.shape:
                continue
            cropped_array = resize_image(cropped_array, self.image_size)
            images.append(cropped_array.astype('float32'))
            classes.append(data[object_arg][4:])
        return images, classes

    def _crop_bounding_box(self,image_array, box_data):
        x_min = int(box_data[0])
        y_min = int(box_data[1])
        x_max = int(box_data[2])
        y_max = int(box_data[3])
        cropped_array = image_array[y_min:y_max, x_min:x_max]
        return cropped_array

    def _shuffle_together(self, array_1, array_2):
        assert len(array_1) == len(array_2)
        num_samples = len(array_1)
        random_args = np.random.permutation(num_samples)
        randomized_list_1 = array_1[random_args]
        randomized_list_2 = array_2[random_args]
        return randomized_list_1, randomized_list_2

    def flow(self, mode='train'):
            while True:
                keys = self._mangage_keys(mode)
                inputs = []
                targets = []
                for key in keys:
                    image_path = self.path_prefix + key + self.suffix
                    image_array = read_image(image_path)
                    original_image_size = image_array.shape[:2]
                    box_data = self.ground_truth_data[key]
                    if mode == 'train' or mode == 'demo':
                        image_array, box_data = self.transform(image_array,
                                                                  box_data)
                    assigned_data = self.box_manager.assign_boxes(box_data)
                    assigned_data = self._denormalize_box(assigned_data,
                                                    original_image_size)
                    images, classes = self._crop_bounding_boxes(
                                            image_array, assigned_data)
                    inputs = inputs + images
                    targets = targets + classes
                    # batch size does not always correspond to the real batch
                    if len(targets) >= self.batch_size:
                        inputs = np.asarray(inputs)
                        targets = np.asarray(targets)
                        inputs, targets = self._shuffle_together(inputs,
                                                                targets)
                        if mode == 'train' or mode == 'val':
                            inputs = preprocess_images(inputs)
                            yield self._wrap_in_dictionary(inputs, targets)
                        if mode == 'demo':
                            yield self._wrap_in_dictionary(inputs, targets)
                        inputs = []
                        targets = []

    def _wrap_in_dictionary(self, image_array, targets):
        return [{'image_array_input':image_array},
                {'predictions':targets}]


if __name__ == "__main__":
    import matplotlib.pyplot as plt

    from prior_box_creator import PriorBoxCreator
    from prior_box_manager import PriorBoxManager
    from utils.utils import split_data
    from XML_parser import XMLParser

    # parameters
    batch_size = 1
    num_epochs = 10000
    image_shape=(70, 50, 3)
    validation_split = .2
    dataset_name = 'VOC2007'
    dataset_root_path = '../datasets/' + dataset_name + '/'
    annotations_path =  dataset_root_path + 'annotations/'
    image_prefix = dataset_root_path + 'images/'
    trained_models_path = '../trained_models/classification/simple_CNN'
    log_file_path = 'classification.log'

    # loading data
    data_manager = XMLParser(annotations_path)
    ground_truth_data = data_manager.get_data(['background', 'bottle'])
    print('Number of real samples:', len(ground_truth_data))
    class_names = data_manager.class_names
    arg_to_class = data_manager.arg_to_class
    num_classes = len(class_names)
    print('Found classes: \n', class_names)

    # creating prior boxes
    prior_box_creator = PriorBoxCreator()
    prior_boxes = prior_box_creator.create_boxes()
    prior_box_manager = PriorBoxManager(prior_boxes, num_classes, overlap_threshold=.5)

    train_keys, val_keys = split_data(ground_truth_data, validation_split)
    image_generator = ImageGenerator(ground_truth_data, prior_box_manager,
                                    batch_size, image_shape[0:2], train_keys,
                                    val_keys, image_prefix,
                                    vertical_flip_probability=0,
                                    suffix='')

    output = next(image_generator.flow('demo'))
    num_objects = len(output[0]['image_array_input'])
    for object_arg in range(num_objects):
        image_array = np.squeeze(output[0]['image_array_input'][object_arg])
        class_arg = np.argmax(output[1]['predictions'][object_arg])
        print(output[1]['predictions'][object_arg])
        class_name = arg_to_class[class_arg]
        plt.title(class_name)
        plt.imshow(image_array.astype('uint8'))
        plt.show()
